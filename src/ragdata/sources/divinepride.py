"""Cliente da API do Divine Pride.

A API é simples (`/api/database/<tipo>/<id>?apiKey=...&server=...`) e permite
**1 requisição por segundo**. Por isso, aqui:

* toda resposta vai para o cache em disco (30 dias por padrão);
* só as chamadas que realmente vão à rede passam pelo limitador;
* os campos são normalizados de forma tolerante — o payload bruto fica
  disponível em `raw` para o caso de o nome de um campo mudar.

Como a normalização não pôde ser verificada contra o serviço real durante o
desenvolvimento, `ragdata doctor` busca um item conhecido e relata quais campos
foram encontrados. Se algo vier vazio, é o primeiro lugar para olhar.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..cache import Cache
from ..config import DIVINE_PRIDE_BASE_URL, Settings, get_settings
from ..errors import ConfigError, NotFound, SourceError
from ..models import Element, Race, Size, TargetMonster
from ..ratelimit import RateLimiter

#: Nomes alternativos aceitos para cada campo normalizado.
_ITEM_FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("id", "itemId"),
    "name": ("name", "unidName", "aegisName"),
    "aegis_name": ("aegisName", "unidName"),
    "description": ("description", "unidDescription"),
    "slots": ("slots", "slot", "slotCount"),
    "attack": ("attack", "atk"),
    "magic_attack": ("magicAttack", "matk"),
    "defense": ("defense", "def"),
    "weight": ("weight",),
    "required_level": ("requiredLevel", "equipLevel", "equipLevelMin"),
    "weapon_level": ("weaponLevel", "itemLevel"),
    "item_type_id": ("itemTypeId", "typeId", "type"),
    "item_subtype_id": ("itemSubTypeId", "subTypeId", "subType"),
    "location": ("location", "equipLocations", "locationId"),
}

_MONSTER_FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("id", "monsterId"),
    "name": ("name",),
    "level": ("level",),
    "hp": ("stats.health", "health", "hp"),
    "attack_min": ("stats.attack.minimum", "attack.minimum"),
    "attack_max": ("stats.attack.maximum", "attack.maximum"),
    "defense": ("stats.defense", "defense", "def"),
    "magic_defense": ("stats.magicDefense", "magicDefense", "mdef"),
    "hit": ("stats.hit", "hit"),
    "flee": ("stats.flee", "flee"),
    "race": ("stats.race", "race"),
    "element": ("stats.element", "element"),
    "element_level": ("stats.elementLevel", "elementLevel"),
    "size": ("stats.scale", "stats.size", "size", "scale"),
    "mvp": ("stats.mvp", "mvp", "isMvp"),
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

_SEARCH_RESULT_RE = re.compile(
    r'href="/database/(?P<kind>item|monster)/(?P<id>\d+)/?[^"]*"[^>]*>(?P<name>[^<]+)<',
    re.IGNORECASE,
)


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


def monster_to_target(data: dict[str, Any]) -> TargetMonster:
    """Converte um monstro normalizado no alvo usado pelas sugestões."""

    def as_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    race_id = as_int(data.get("race"))
    element_id = as_int(data.get("element"))
    size_id = as_int(data.get("size"))

    # O Divine Pride às vezes codifica elemento como `nível*20 + elemento`.
    element_level = as_int(data.get("element_level"))
    if element_id is not None and element_id >= 20:
        element_level = element_level or (element_id // 20)
        element_id = element_id % 20

    return TargetMonster(
        name=str(data.get("name") or "desconhecido"),
        monster_id=as_int(data.get("id")),
        level=as_int(data.get("level")),
        hp=as_int(data.get("hp")),
        race=_RACE_BY_ID.get(race_id) if race_id is not None else None,
        element=_ELEMENT_BY_ID.get(element_id) if element_id is not None else None,
        element_level=element_level if element_level in (1, 2, 3, 4) else None,
        size=_SIZE_BY_ID.get(size_id) if size_id is not None else None,
        defense=as_int(data.get("defense")),
        magic_defense=as_int(data.get("magic_defense")),
        flee=as_int(data.get("flee")),
        hit=as_int(data.get("hit")),
        is_mvp=bool(data.get("mvp")),
    )


class DivinePrideClient:
    """Consulta itens, monstros e habilidades no Divine Pride."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        cache: Cache | None = None,
        limiter: RateLimiter | None = None,
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

    def _get_json(self, kind: str, entity_id: int, *, refresh: bool = False) -> dict[str, Any]:
        server = self.settings.divine_pride_server
        cache_key = f"dp:{server}:{kind}:{entity_id}"
        if not refresh:
            cached = self.cache.get_json(cache_key)
            if isinstance(cached, dict):
                return cached

        api_key = self._require_key()
        self.limiter.acquire()
        try:
            response = self._client.get(
                f"/api/database/{kind}/{entity_id}",
                params={"apiKey": api_key, "server": server},
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"Falha ao consultar o Divine Pride: {exc}") from exc

        if response.status_code == 404:
            raise NotFound(f"{kind} {entity_id} não existe no Divine Pride (servidor {server}).")
        if response.status_code in (401, 403):
            raise ConfigError("Chave da API do Divine Pride recusada (401/403). Confira DIVINE_PRIDE_API_KEY.")
        if response.status_code == 429:
            raise SourceError("Divine Pride respondeu 429: limite de 1 req/s excedido.")
        if response.status_code >= 400:
            raise SourceError(f"Divine Pride respondeu {response.status_code} para {kind} {entity_id}.")

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise SourceError(f"Resposta do Divine Pride não é JSON válido: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourceError(f"Resposta inesperada do Divine Pride para {kind} {entity_id}.")

        self.cache.set_json(cache_key, payload)
        return payload

    # --- API pública --------------------------------------------------

    def item(self, item_id: int, *, refresh: bool = False) -> dict[str, Any]:
        """Item por ID, com os campos normalizados e o payload bruto."""
        return normalize_item(self._get_json("Item", item_id, refresh=refresh))

    def monster(self, monster_id: int, *, refresh: bool = False) -> dict[str, Any]:
        """Monstro por ID."""
        return normalize_monster(self._get_json("Monster", monster_id, refresh=refresh))

    def monster_target(self, monster_id: int, *, refresh: bool = False) -> TargetMonster:
        """Monstro por ID, já no formato de alvo para as sugestões."""
        return monster_to_target(self.monster(monster_id, refresh=refresh))

    def skill(self, skill_id: int, *, refresh: bool = False) -> dict[str, Any]:
        """Habilidade por ID (payload bruto — o formato varia bastante)."""
        return self._get_json("Skill", skill_id, refresh=refresh)

    def search(self, query: str, *, kind: str = "item", limit: int = 10) -> list[dict[str, Any]]:
        """Busca por nome na página de busca do site.

        A API pública só responde por ID, então isto lê a página de busca e
        extrai os links de resultado. É **melhor-esforço**: se o site mudar o
        HTML, a lista volta vazia (e nunca levanta exceção por isso). Para algo
        determinístico, use `item()`/`monster()` com o ID.
        """
        cache_key = f"dp:search:{kind}:{query.strip().casefold()}"
        html = self.cache.get(cache_key)
        if html is None:
            self.limiter.acquire()
            try:
                response = self._client.get("/database/search", params={"q": query})
            except httpx.HTTPError as exc:
                raise SourceError(f"Falha na busca do Divine Pride: {exc}") from exc
            if response.status_code >= 400:
                raise SourceError(f"Busca do Divine Pride respondeu {response.status_code}.")
            html = response.text
            self.cache.set(cache_key, html)

        results: list[dict[str, Any]] = []
        seen: set[int] = set()
        for match in _SEARCH_RESULT_RE.finditer(html):
            if match.group("kind").lower() != kind.lower():
                continue
            entity_id = int(match.group("id"))
            if entity_id in seen:
                continue
            seen.add(entity_id)
            results.append({"id": entity_id, "name": match.group("name").strip()})
            if len(results) >= limit:
                break
        return results
