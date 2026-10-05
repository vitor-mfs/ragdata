"""Cliente do Divine Pride: API (por ID) e listagem do site (por filtro).

A API (https://www.divine-pride.net/tools/api-doc) responde em
`GET /api/database/{Item|Monster|Skill|Efst}/{id}?apiKey=...`. O servidor vai no
header `x-server` (`LATAM`, `bRO`, `kROM`, ...) e o idioma em `Accept-Language`
(`pt`, `en`, ...). A resposta traz `region`, e conferimos que é o servidor
pedido — usar silenciosamente dados de outra região seria pior do que falhar.

A API **não tem busca** e proíbe enumerar IDs em massa. Por isso a descoberta de
itens (por nome, por função do item, por texto na descrição) usa a **listagem do
site** (`/database/item/<categoria>?function=..&description=..`). Com
`Accept-Language: pt-BR` o site serve a base LATAM em português e cada linha da
tabela diz em quais servidores o item existe — é esse badge que garante que só
itens listados no LATAM sejam considerados. Depois, só os itens de interesse são
lidos pela API, um a um, com cache em disco e espaçamento de 1 req/s.

Toda resposta vai para o cache; só o que realmente vai à rede passa pelo
limitador. Os campos são normalizados de forma tolerante — o payload bruto fica
em `raw`. `ragdata doctor` confere a normalização contra o serviço real.
"""

from __future__ import annotations

import html as html_lib
import json
import math
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from ..cache import Cache
from ..config import DIVINE_PRIDE_BASE_URL, DIVINE_PRIDE_SERVERS, Settings, get_settings
from ..errors import ConfigError, NotFound, SourceError, WrongRegion
from ..models import Element, Race, Size, TargetMonster
from ..ratelimit import RateLimiter

#: Alias da base do Ragnarok LATAM no Divine Pride.
LATAM = "LATAM"

#: A listagem do site pagina de 20 em 20.
LISTING_PAGE_SIZE = 20

#: Listagens mudam quando entram itens novos; validade menor do que a dos itens.
LISTING_TTL_SECONDS = 24 * 3600

#: Em 429, só esperamos e repetimos se o `Retry-After` for curto.
MAX_RETRY_AFTER_SECONDS = 30

#: Categorias da listagem do site (`/database/item/<categoria>`).
ITEM_CATEGORIES: tuple[str, ...] = (
    "weapon", "armor", "card", "consumable", "ammo", "costume", "shadow", "other",
)

#: Funções de item do filtro `function` da listagem (IDs internos do Divine Pride).
ITEM_FUNCTIONS: dict[int, str] = {
    17: "Increases a stat by a value",
    18: "Decreases a stat by a value",
    21: "Increases damage to a race",
    23: "Reduce damage taken from a property",
    24: "Increase damage taken from a property",
    25: "Reduce damage taken from a race",
    26: "Increase damage taken from a race",
    27: "Increases damage to a property",
    29: "Increase damage to a size",
    33: "Increase damage of a skill",
    43: "Gain HP/SP for every kill",
    99: "Increases experience for a monster race",
    400: "Increases the healing done by skills and items",
    411: "Increase magic damage to a size",
    417: "Ignores the MDef of a monster by class",
    577: "Reduces the cooldown time for a skill",
    689: "Increase magic damage to a property",
}

#: Nomes alternativos aceitos para cada campo normalizado de item.
_ITEM_FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("id", "itemId"),
    "name": ("name", "unidName"),
    "aegis_name": ("aegisName", "unidName"),
    "description": ("description", "unidDescription"),
    "slots": ("slots", "slot", "slotCount"),
    "attack": ("attack", "atk"),
    "magic_attack": ("magicAttack", "matk"),
    "defense": ("defense", "def"),
    "weight": ("weight",),
    "required_level": ("requiredLevel", "equipLevel", "equipLevelMin"),
    "weapon_level": ("weaponLevel", "itemLevel"),
    "type": ("type", "itemType"),
    "sub_type": ("subType", "itemSubType"),
    "item_type_id": ("itemTypeId", "typeId"),
    "item_subtype_id": ("itemSubTypeId", "subTypeId"),
    "location": ("location", "equipLocations", "locationId"),
    "region": ("region", "server"),
    "icon_url": ("iconUrl",),
    "sell_price": ("sellPrice",),
    "buy_price": ("buyPrice",),
    "all_jobs_allowed": ("allJobsAllowed",),
    "allowed_job_ids": ("allowedJobIds", "jobs"),
    "sources": ("sources",),
    "scripts": ("scripts",),
    "updated_at": ("dataUpdatedAtUtc", "lastUpdate"),
}

#: Campos de item que podem legitimamente vir vazios (armadura não tem ATK etc.).
ITEM_OPTIONAL_FIELDS: frozenset[str] = frozenset(
    {
        "slots", "attack", "magic_attack", "defense", "required_level", "weapon_level",
        "item_type_id", "item_subtype_id", "location", "sub_type", "icon_url",
        "sell_price", "buy_price", "all_jobs_allowed", "allowed_job_ids", "sources",
        "scripts", "updated_at", "aegis_name",
    }
)

_MONSTER_FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("id", "monsterId"),
    "name": ("name",),
    "level": ("level",),
    "hp": ("stats.health", "health", "hp"),
    "attack_min": ("stats.attack.minimum", "attack.minimum"),
    "attack_max": ("stats.attack.maximum", "attack.maximum"),
    "defense": ("stats.defense", "defense", "def"),
    "magic_defense": ("stats.magicDefense", "magicDefense", "mDef", "mdef"),
    "hit": ("stats.hit", "hit"),
    "flee": ("stats.flee", "flee"),
    "race": ("stats.race", "race"),
    "element": ("stats.element", "element"),
    "element_level": ("stats.elementLevel", "elementLevel"),
    "size": ("stats.scale", "stats.size", "size", "scale"),
    "mvp": ("stats.mvp", "mvp", "isMvp"),
    "monster_type": ("type", "monsterType"),
    "region": ("region", "server"),
}

#: Códigos numéricos usados pelo Divine Pride/rAthena.
_RACE_BY_ID = {
    0: Race.FORMLESS, 1: Race.UNDEAD, 2: Race.BRUTE, 3: Race.PLANT, 4: Race.INSECT,
    5: Race.FISH, 6: Race.DEMON, 7: Race.DEMIHUMAN, 8: Race.ANGEL, 9: Race.DRAGON,
    10: Race.PLAYER,
}
_ELEMENT_BY_ID = {
    0: Element.NEUTRAL, 1: Element.WATER, 2: Element.EARTH, 3: Element.FIRE,
    4: Element.WIND, 5: Element.POISON, 6: Element.HOLY, 7: Element.SHADOW,
    8: Element.GHOST, 9: Element.UNDEAD,
}
_SIZE_BY_ID = {0: Size.SMALL, 1: Size.MEDIUM, 2: Size.LARGE}

#: A API atual devolve raça/elemento/tamanho por nome em inglês.
_RACE_BY_NAME = {
    "formless": Race.FORMLESS, "undead": Race.UNDEAD, "brute": Race.BRUTE,
    "plant": Race.PLANT, "insect": Race.INSECT, "fish": Race.FISH, "demon": Race.DEMON,
    "demihuman": Race.DEMIHUMAN, "demi-human": Race.DEMIHUMAN, "demi human": Race.DEMIHUMAN,
    "angel": Race.ANGEL, "dragon": Race.DRAGON, "player": Race.PLAYER,
}
_ELEMENT_BY_NAME = {
    "neutral": Element.NEUTRAL, "water": Element.WATER, "earth": Element.EARTH,
    "fire": Element.FIRE, "wind": Element.WIND, "poison": Element.POISON,
    "holy": Element.HOLY, "shadow": Element.SHADOW, "dark": Element.SHADOW,
    "ghost": Element.GHOST, "undead": Element.UNDEAD,
}
_SIZE_BY_NAME = {"small": Size.SMALL, "medium": Size.MEDIUM, "large": Size.LARGE}

_SEARCH_RESULT_RE = re.compile(
    r'href="/database/(?P<kind>item|monster|skill)/(?P<id>\d+)/?[^"]*"[^>]*>(?P<name>[^<]*)<',
    re.IGNORECASE,
)

# --- HTML da listagem de itens -----------------------------------------------

_TABLE_HEAD_RE = re.compile(r"<thead[^>]*>(.*?)</thead>", re.S | re.I)
_TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.S | re.I)
_ROW_RE = re.compile(
    r"<tr[^>]*window\.location='/database/item/(?P<id>\d+)'[^>]*>(?P<body>.*?)</tr>",
    re.S | re.I,
)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_ANCHOR_RE = re.compile(r'<a\s[^>]*href="/database/item/\d+"[^>]*>(.*?)</a>', re.S | re.I)
_BADGE_RE = re.compile(r'<span\s+class="badge[^"]*"[^>]*>([^<]*)</span>', re.I)
_RESULTS_RE = re.compile(r"([\d][\d.,]*)\s+results?\b", re.I)
_REGION_RE = re.compile(r"<span>\s*([A-Za-z]+)\s*&middot;\s*([A-Za-z]+)\s*</span>")
_PAGE_LINK_RE = re.compile(r"(?:[?&]|&amp;)page=(\d+)")
_SLOTS_SUFFIX_RE = re.compile(r"\[(\d)\]\s*$")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

#: Nomes que a listagem mostra quando o item não tem nome na base/idioma pedido.
_EMPTY_NAMES = {"", "(null)", "null"}


def _text(fragment: str) -> str:
    """Texto de um trecho de HTML: sem tags, sem entidades, espaços colapsados."""
    return _WS_RE.sub(" ", html_lib.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).replace(".", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ItemRow:
    """Uma linha da listagem de itens do site."""

    id: int
    name: str
    type: str
    sub_type: str
    required_level: int | None
    servers: tuple[str, ...]

    @property
    def slots(self) -> int | None:
        """Slots deduzidos do sufixo `[n]` do nome, como o site exibe."""
        match = _SLOTS_SUFFIX_RE.search(self.name)
        return int(match.group(1)) if match else None

    @property
    def on_latam(self) -> bool:
        return any(server.casefold() == LATAM.casefold() for server in self.servers)

    @property
    def has_name(self) -> bool:
        """False para itens sem nome traduzido (a listagem mostra só `[1]` ou nada)."""
        bare = _SLOTS_SUFFIX_RE.sub("", self.name).strip()
        return bare.casefold() not in _EMPTY_NAMES

    @property
    def url(self) -> str:
        return f"{DIVINE_PRIDE_BASE_URL}/database/item/{self.id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nome": self.name,
            "tipo": self.type,
            "subtipo": self.sub_type,
            "nivel_necessario": self.required_level,
            "slots": self.slots,
            "servidores": list(self.servers),
            "url": self.url,
        }


@dataclass
class ItemListing:
    """Uma página da listagem de itens."""

    rows: list[ItemRow] = field(default_factory=list)
    total: int | None = None
    page: int = 1
    region: str | None = None
    language: str | None = None
    page_size: int = LISTING_PAGE_SIZE
    #: Maior número de página visto nos links de paginação (quando `total` falta).
    last_page_seen: int | None = None

    @property
    def pages(self) -> int:
        """Total de páginas: pelo contador de resultados e pelos links de paginação, o que for maior."""
        if self.total is not None and not self.total and not self.rows:
            return 0
        from_total = math.ceil(self.total / self.page_size) if self.total else 0
        from_links = self.last_page_seen or 0
        current = self.page if (self.rows or from_total or from_links) else 0
        return max(from_total, from_links, current)


def parse_item_listing(page_html: str) -> ItemListing:
    """Extrai as linhas, o total e a região/idioma de uma página de listagem.

    Tolerante por desenho: se o site mudar o HTML, o resultado vem vazio em vez
    de levantar exceção — quem chama decide o que fazer com zero linhas.
    """
    listing = ItemListing()

    region = _REGION_RE.search(page_html)
    if region:
        listing.region, listing.language = region.group(1), region.group(2)

    total = _RESULTS_RE.search(page_html)
    if total:
        listing.total = _to_int(total.group(1))

    pages = [int(p) for p in _PAGE_LINK_RE.findall(page_html)]
    if pages:
        listing.last_page_seen = max(pages)

    columns: dict[str, int] = {}
    head = _TABLE_HEAD_RE.search(page_html)
    if head:
        for index, raw in enumerate(_TH_RE.findall(head.group(1))):
            columns.setdefault(_text(raw).casefold(), index)

    def column(cells: list[str], name: str, fallback: int | None) -> str:
        index = columns.get(name, fallback)
        if index is None:
            return ""
        if index < 0:
            index += len(cells)
        return cells[index] if 0 <= index < len(cells) else ""

    for match in _ROW_RE.finditer(page_html):
        cells = _TD_RE.findall(match.group("body"))
        if not cells:
            continue
        name_cell = column(cells, "name", 0)
        anchor = _ANCHOR_RE.search(name_cell)
        name = _text(anchor.group(1)) if anchor else _text(name_cell)
        servers = tuple(_text(b) for b in _BADGE_RE.findall(column(cells, "server", -1)))
        listing.rows.append(
            ItemRow(
                id=int(match.group("id")),
                name=name,
                type=_text(column(cells, "type", 1)),
                sub_type=_text(column(cells, "subtype", 2)),
                required_level=_to_int(_text(column(cells, "required level", None))),
                servers=servers,
            )
        )
    return listing


def site_accept_language(language: str) -> str:
    """`Accept-Language` que leva o site ao idioma (e à base) desejados.

    Para anônimos, `pt-BR` faz o site servir a base LATAM em português — é o
    que a busca por necessidade precisa. Outros idiomas vão como estão.
    """
    lang = (language or "").strip().casefold()
    if lang in ("pt", "pt-br", "pt_br"):
        return "pt-BR,pt;q=0.9"
    return language


# --- normalização -----------------------------------------------------------


def _dig(payload: Any, path: str) -> Any:
    """Lê `a.b.c` em dicionários aninhados, devolvendo None se faltar."""
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _pick(payload: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    for candidate in candidates:
        value = _dig(payload, candidate)
        if value not in (None, ""):
            return value
    return None


def normalize_item(payload: dict[str, Any]) -> dict[str, Any]:
    """Achata o JSON de item do Divine Pride nos campos que usamos."""
    out = {key: _pick(payload, names) for key, names in _ITEM_FIELDS.items()}
    out["raw"] = payload
    return out


def normalize_monster(payload: dict[str, Any]) -> dict[str, Any]:
    """Achata o JSON de monstro do Divine Pride."""
    out = {key: _pick(payload, names) for key, names in _MONSTER_FIELDS.items()}
    out["raw"] = payload
    return out


def _lookup(value: Any, by_id: dict[int, Any], by_name: dict[str, Any]) -> Any:
    """Resolve um código numérico ou um nome em inglês para o enum correspondente."""
    if value is None:
        return None
    try:
        return by_id.get(int(value))
    except (TypeError, ValueError):
        return by_name.get(str(value).strip().casefold())


def monster_to_target(data: dict[str, Any]) -> TargetMonster:
    """Converte um monstro normalizado no alvo usado pelas sugestões."""

    def as_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    element_raw = data.get("element")
    element_id = as_int(element_raw)
    element_level = as_int(data.get("element_level"))
    # O Divine Pride às vezes codifica elemento como `nível*20 + elemento`.
    if element_id is not None and element_id >= 20:
        element_level = element_level or (element_id // 20)
        element_id = element_id % 20
    element = (
        _ELEMENT_BY_ID.get(element_id)
        if element_id is not None
        else _lookup(element_raw, _ELEMENT_BY_ID, _ELEMENT_BY_NAME)
    )

    monster_type = str(data.get("monster_type") or "").strip().casefold()

    return TargetMonster(
        name=str(data.get("name") or "desconhecido"),
        monster_id=as_int(data.get("id")),
        level=as_int(data.get("level")),
        hp=as_int(data.get("hp")),
        race=_lookup(data.get("race"), _RACE_BY_ID, _RACE_BY_NAME),
        element=element,
        element_level=element_level if element_level in (1, 2, 3, 4) else None,
        size=_lookup(data.get("size"), _SIZE_BY_ID, _SIZE_BY_NAME),
        defense=as_int(data.get("defense")),
        magic_defense=as_int(data.get("magic_defense")),
        flee=as_int(data.get("flee")),
        hit=as_int(data.get("hit")),
        is_mvp=bool(data.get("mvp")) or monster_type == "mvp",
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


class DivinePrideClient:
    """Consulta itens, monstros e habilidades no Divine Pride."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        cache: Cache | None = None,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=DIVINE_PRIDE_BASE_URL,
            timeout=self.settings.http_timeout,
            headers={"User-Agent": self.settings.user_agent},
            follow_redirects=True,
        )
        self.cache = cache or Cache(self.settings.http_cache_path, self.settings.cache_ttl_seconds)
        self.limiter = limiter or RateLimiter(self.settings.divine_pride_rate_limit)
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> DivinePrideClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- internos -----------------------------------------------------

    def _require_key(self) -> str:
        key = self.settings.divine_pride_api_key
        if not key:
            raise ConfigError(
                "Falta a chave da API do Divine Pride. "
                "Pegue a sua em https://www.divine-pride.net/account e exporte "
                "DIVINE_PRIDE_API_KEY."
            )
        return key

    def _api_get(self, path: str, *, params: dict[str, str], headers: dict[str, str], what: str) -> Any:
        """GET na API com tratamento de erros e uma repetição em 429 curto."""
        for attempt in (1, 2):
            self.limiter.acquire()
            try:
                response = self._client.get(path, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise SourceError(f"Falha ao consultar o Divine Pride: {exc}") from exc

            if response.status_code == 429:
                retry_after = _retry_after_seconds(response)
                if attempt == 1 and retry_after is not None and retry_after <= MAX_RETRY_AFTER_SECONDS:
                    self._sleep(retry_after)
                    continue
                espera = f"{retry_after:.0f} s" if retry_after is not None else "alguns segundos"
                raise SourceError(
                    f"Divine Pride respondeu 429: limite de requisições excedido (o ragdata já "
                    f"espaça 1 req/s). Aguarde {espera} e tente de novo."
                )
            break

        if response.status_code == 404:
            raise NotFound(f"{what} não existe no Divine Pride (servidor {headers.get('x-server')}).")
        if response.status_code in (401, 403):
            raise ConfigError("Chave da API do Divine Pride recusada (401/403). Confira DIVINE_PRIDE_API_KEY.")
        if response.status_code == 400:
            raise SourceError(
                f"Divine Pride respondeu 400 para {what}: parâmetro inválido. Confira RAGDATA_DP_SERVER "
                f"(aliases: {', '.join(DIVINE_PRIDE_SERVERS)}) e RAGDATA_DP_LANGUAGE."
            )
        if response.status_code >= 400:
            raise SourceError(f"Divine Pride respondeu {response.status_code} para {what}.")

        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise SourceError(f"Resposta do Divine Pride não é JSON válido: {exc}") from exc

    def _get_json(
        self,
        kind: str,
        entity_id: int,
        *,
        refresh: bool = False,
        server: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        server = server or self.settings.divine_pride_server
        language = language or self.settings.divine_pride_language
        cache_key = f"dp:{server}:{language}:{kind}:{entity_id}"
        if not refresh:
            cached = self.cache.get_json(cache_key)
            if isinstance(cached, dict):
                return cached

        api_key = self._require_key()
        payload = self._api_get(
            f"/api/database/{kind}/{entity_id}",
            # `server` na query é o contrato antigo; o atual é o header `x-server`.
            params={"apiKey": api_key, "server": server},
            headers={"x-server": server, "Accept-Language": language},
            what=f"{kind} {entity_id}",
        )
        if not isinstance(payload, dict):
            raise SourceError(f"Resposta inesperada do Divine Pride para {kind} {entity_id}.")

        region = payload.get("region")
        if isinstance(region, str) and region.strip() and region.strip().casefold() != server.casefold():
            raise WrongRegion(
                f"{kind} {entity_id}",
                expected=server,
                received=region.strip(),
                hint=f"Confira RAGDATA_DP_SERVER (aliases válidos: {', '.join(DIVINE_PRIDE_SERVERS)}).",
            )

        self.cache.set_json(cache_key, payload)
        return payload

    def _site_get(self, path: str, params: list[tuple[str, str]], accept_language: str) -> str:
        """GET em uma página do site (HTML), passando pelo limitador."""
        self.limiter.acquire()
        try:
            response = self._client.get(path, params=params, headers={"Accept-Language": accept_language})
        except httpx.HTTPError as exc:
            raise SourceError(f"Falha ao consultar o site do Divine Pride: {exc}") from exc
        if response.status_code == 429:
            raise SourceError("Site do Divine Pride respondeu 429 (limite de requisições). Tente mais tarde.")
        if response.status_code >= 400:
            raise SourceError(f"Site do Divine Pride respondeu {response.status_code} para {path}.")
        return response.text

    # --- API pública --------------------------------------------------

    def item(
        self,
        item_id: int,
        *,
        refresh: bool = False,
        server: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Item por ID, com os campos normalizados e o payload bruto."""
        return normalize_item(self._get_json("Item", item_id, refresh=refresh, server=server, language=language))

    def monster(
        self,
        monster_id: int,
        *,
        refresh: bool = False,
        server: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Monstro por ID."""
        return normalize_monster(
            self._get_json("Monster", monster_id, refresh=refresh, server=server, language=language)
        )

    def monster_target(self, monster_id: int, *, refresh: bool = False) -> TargetMonster:
        """Monstro por ID, já no formato de alvo para as sugestões."""
        return monster_to_target(self.monster(monster_id, refresh=refresh))

    def skill(self, skill_id: int, *, refresh: bool = False) -> dict[str, Any]:
        """Habilidade por ID (payload bruto — o formato varia bastante)."""
        return self._get_json("Skill", skill_id, refresh=refresh)

    def search(
        self,
        query: str,
        *,
        kind: str = "item",
        limit: int = 10,
        include_description: bool = False,
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        """Busca por nome na página de busca do site (`/database?q=...`).

        A API pública só responde por ID, então isto lê a página de busca e
        extrai os links de resultado. É **melhor-esforço**: se o site mudar o
        HTML, a lista volta vazia (e nunca levanta exceção por isso). Para algo
        determinístico, use `item()`/`monster()` com o ID.
        """
        accept = site_accept_language(language or self.settings.divine_pride_language)
        cache_key = f"dp:search:{accept}:{kind}:{int(include_description)}:{query.strip().casefold()}"
        page_html = self.cache.get(cache_key)
        if page_html is None:
            params = [("q", query)]
            if include_description:
                params.append(("includeDescription", "true"))
            page_html = self._site_get("/database", params, accept)
            self.cache.set(cache_key, page_html)

        results: list[dict[str, Any]] = []
        seen: set[int] = set()
        for match in _SEARCH_RESULT_RE.finditer(page_html):
            if match.group("kind").lower() != kind.lower():
                continue
            entity_id = int(match.group("id"))
            name = _text(match.group("name"))
            if entity_id in seen or name.casefold() in _EMPTY_NAMES:
                continue
            seen.add(entity_id)
            results.append({"id": entity_id, "name": name})
            if len(results) >= limit:
                break
        return results

    def list_items(
        self,
        category: str,
        *,
        query: str | None = None,
        function_id: int | None = None,
        description: str | None = None,
        sub_types: Iterable[str] = (),
        job_ids: Iterable[int] = (),
        min_level: int | None = None,
        max_level: int | None = None,
        sort_by: str | None = None,
        sort_direction: str = "asc",
        page: int = 1,
        language: str = "pt",
        refresh: bool = False,
    ) -> ItemListing:
        """Uma página da listagem de itens do site, com os filtros do próprio site.

        `function_id` é uma das `ITEM_FUNCTIONS`; `description` é texto que
        precisa aparecer na descrição (no idioma da base servida). Cada linha
        traz os servidores em que o item existe — filtre por `ItemRow.on_latam`.
        """
        category = category.strip().casefold()
        if category not in ITEM_CATEGORIES:
            raise ValueError(f"Categoria desconhecida: {category!r}. Use uma de: {', '.join(ITEM_CATEGORIES)}.")

        params: list[tuple[str, str]] = []
        if query:
            params.append(("query", query))
        if function_id is not None:
            params.append(("function", str(function_id)))
        if description:
            params.append(("description", description))
        params.extend(("subTypes", sub_type) for sub_type in sub_types)
        params.extend(("jobGroups", str(job_id)) for job_id in job_ids)
        if min_level is not None:
            params.append(("minRequiredLevel", str(min_level)))
        if max_level is not None:
            params.append(("maxRequiredLevel", str(max_level)))
        if sort_by:
            params.extend((("sortBy", sort_by), ("sortDirection", sort_direction)))
        if page > 1:
            params.append(("page", str(page)))

        accept = site_accept_language(language)
        cache_key = f"dp:list:{accept}:{category}:{urlencode(params)}"
        ttl = min(self.cache.ttl_seconds, LISTING_TTL_SECONDS) if self.cache.ttl_seconds >= 0 else LISTING_TTL_SECONDS
        page_html = None if refresh else self.cache.get(cache_key, ttl_seconds=ttl)
        if page_html is None:
            page_html = self._site_get(f"/database/item/{category}", params, accept)
            self.cache.set(cache_key, page_html)

        listing = parse_item_listing(page_html)
        listing.page = page
        return listing
