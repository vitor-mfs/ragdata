"""Busca de armas e armaduras a partir de uma necessidade.

"Resistência a Dragão", "dano em Amorfo", "dano de [Sopro do Dragão]": a
necessidade vira (1) uma **função de item** do Divine Pride e (2) uma
**palavra-chave em português** que precisa aparecer na descrição. Com isso a
listagem do site devolve poucos candidatos; só eles são lidos pela API — a API
não tem busca e proíbe enumerar IDs em massa.

Regras do LATAM, nessa ordem:

* a listagem é pedida na base LATAM em português e só entram linhas com o badge
  `LATAM` e com nome em português (itens sem nome na base são descartados);
* os detalhes vêm da API com `x-server: LATAM`; se a resposta vier de outra
  região ou com 404, o item é descartado;
* as linhas da descrição que citam a necessidade são extraídas e o item é
  ranqueado pelo maior percentual encontrado. Candidato sem linha reconhecível
  fica em `possiveis`, nunca somem — o texto do jogo é a fonte da verdade.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import NotFound, SourceError, WrongRegion
from .jobs import canonical_job_key, normalize
from .sources.divinepride import LATAM, DivinePrideClient, ItemRow


class NeedKind(StrEnum):
    """Tipos de necessidade suportados (chave em português, estável para a CLI/MCP)."""

    RESIST_RACE = "resistencia_raca"
    DAMAGE_RACE = "dano_raca"
    MAGIC_DAMAGE_RACE = "dano_magico_raca"
    RESIST_ELEMENT = "resistencia_elemento"
    DAMAGE_ELEMENT = "dano_elemento"
    MAGIC_DAMAGE_ELEMENT = "dano_magico_elemento"
    DAMAGE_SIZE = "dano_tamanho"
    MAGIC_DAMAGE_SIZE = "dano_magico_tamanho"
    SKILL_DAMAGE = "dano_habilidade"
    SKILL_COOLDOWN = "recarga_habilidade"
    STAT = "atributo"


@dataclass(frozen=True)
class Term:
    """Como um alvo aparece no texto do LATAM, e como o jogador costuma escrevê-lo."""

    keyword: str
    aliases: tuple[str, ...] = ()


#: Raças, como escritas nas descrições do LATAM ("raça Dragão").
RACES: dict[str, Term] = {
    "amorfo": Term("Amorfo", ("formless", "sem forma", "amorfos")),
    "morto-vivo": Term("Morto-Vivo", ("undead", "mortos-vivos", "morto vivo", "mortos vivos", "zumbi")),
    "bruto": Term("Bruto", ("brute", "brutos", "animal", "fera")),
    "planta": Term("Planta", ("plant", "plantas")),
    "inseto": Term("Inseto", ("insect", "insetos")),
    "peixe": Term("Peixe", ("fish", "peixes")),
    "demonio": Term("Demônio", ("demon", "demonios", "demônios")),
    "humanoide": Term("Humanoide", ("demi-human", "demihuman", "demi humano", "demi-humano", "humano", "humanos")),
    "anjo": Term("Anjo", ("angel", "anjos")),
    "dragao": Term("Dragão", ("dragon", "dragoes", "dragões")),
    "jogador": Term("Jogador", ("player", "jogadores", "players")),
}

#: Propriedades (elementos), como escritas no LATAM ("propriedade Fogo").
ELEMENTS: dict[str, Term] = {
    "neutro": Term("Neutro", ("neutral", "neutra")),
    "agua": Term("Água", ("water",)),
    "terra": Term("Terra", ("earth",)),
    "fogo": Term("Fogo", ("fire",)),
    "vento": Term("Vento", ("wind",)),
    "veneno": Term("Veneno", ("poison",)),
    "sagrado": Term("Sagrado", ("holy", "sagrada")),
    "sombrio": Term("Sombrio", ("shadow", "dark", "sombra", "trevas", "sombria")),
    "fantasma": Term("Fantasma", ("ghost",)),
    "maldito": Term("Maldito", ("undead", "maldita", "morto-vivo", "morto vivo")),
}

SIZES: dict[str, Term] = {
    "pequeno": Term("Pequeno", ("small", "pequenos")),
    "medio": Term("Médio", ("medium", "medios", "médios")),
    "grande": Term("Grande", ("large", "grandes")),
}

#: Atributos, pela sigla usada no cliente LATAM.
STATS: dict[str, Term] = {
    "for": Term("FOR", ("str", "forca", "força")),
    "agi": Term("AGI", ("agilidade",)),
    "vit": Term("VIT", ("vitalidade",)),
    "int": Term("INT", ("inteligencia", "inteligência")),
    "des": Term("DES", ("dex", "destreza")),
    "sor": Term("SOR", ("luk", "sorte")),
}

VOCABULARIES: dict[str, dict[str, Term]] = {
    "raca": RACES,
    "elemento": ELEMENTS,
    "tamanho": SIZES,
    "atributo": STATS,
}


@dataclass(frozen=True)
class KindSpec:
    """Como um tipo de necessidade é buscado e reconhecido no texto."""

    kind: NeedKind
    function_id: int
    vocabulary: str | None  # chave de VOCABULARIES, ou None para texto livre (habilidade)
    label: str  # com {alvo}
    cues: tuple[str, ...]  # a linha precisa conter um destes (normalizado)
    excludes: tuple[str, ...] = ()  # ...e nenhum destes
    bracketed: bool = False  # a palavra-chave aparece entre colchetes ("Dano de [X]")


_DAMAGE_EXCLUDES = ("recebido", "resist", "exp ", "experiencia", "sofrido")

KIND_SPECS: dict[NeedKind, KindSpec] = {
    NeedKind.RESIST_RACE: KindSpec(
        NeedKind.RESIST_RACE, 25, "raca", "Resistência a raça {alvo}", ("resist", "reduz"),
    ),
    NeedKind.DAMAGE_RACE: KindSpec(
        NeedKind.DAMAGE_RACE, 21, "raca", "Dano contra a raça {alvo}", ("dano",), _DAMAGE_EXCLUDES,
    ),
    NeedKind.MAGIC_DAMAGE_RACE: KindSpec(
        NeedKind.MAGIC_DAMAGE_RACE, 21, "raca", "Dano mágico contra a raça {alvo}",
        ("dano magico",), _DAMAGE_EXCLUDES,
    ),
    NeedKind.RESIST_ELEMENT: KindSpec(
        NeedKind.RESIST_ELEMENT, 23, "elemento", "Resistência a propriedade {alvo}", ("resist", "reduz"),
    ),
    NeedKind.DAMAGE_ELEMENT: KindSpec(
        NeedKind.DAMAGE_ELEMENT, 27, "elemento", "Dano contra a propriedade {alvo}", ("dano",), _DAMAGE_EXCLUDES,
    ),
    NeedKind.MAGIC_DAMAGE_ELEMENT: KindSpec(
        NeedKind.MAGIC_DAMAGE_ELEMENT, 689, "elemento", "Dano mágico de/contra a propriedade {alvo}",
        ("dano magico",), _DAMAGE_EXCLUDES,
    ),
    NeedKind.DAMAGE_SIZE: KindSpec(
        NeedKind.DAMAGE_SIZE, 29, "tamanho", "Dano contra o tamanho {alvo}", ("dano",), _DAMAGE_EXCLUDES,
    ),
    NeedKind.MAGIC_DAMAGE_SIZE: KindSpec(
        NeedKind.MAGIC_DAMAGE_SIZE, 411, "tamanho", "Dano mágico contra o tamanho {alvo}",
        ("dano magico",), _DAMAGE_EXCLUDES,
    ),
    NeedKind.SKILL_DAMAGE: KindSpec(
        NeedKind.SKILL_DAMAGE, 33, None, "Dano de [{alvo}]", ("dano",), ("recarga", "recebido"), bracketed=True,
    ),
    NeedKind.SKILL_COOLDOWN: KindSpec(
        NeedKind.SKILL_COOLDOWN, 577, None, "Recarga de [{alvo}]", ("recarga",), bracketed=True,
    ),
    NeedKind.STAT: KindSpec(NeedKind.STAT, 17, "atributo", "{alvo}", ()),
}


@dataclass(frozen=True)
class Need:
    """Uma necessidade já resolvida: tipo + alvo + palavra-chave a procurar."""

    kind: NeedKind
    target: str  # chave canônica (ex.: "dragao") ou nome da habilidade
    keyword: str  # como aparece no texto do LATAM (ex.: "Dragão")

    @property
    def spec(self) -> KindSpec:
        return KIND_SPECS[self.kind]

    @property
    def label(self) -> str:
        return self.spec.label.format(alvo=self.keyword)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tipo": self.kind.value,
            "alvo": self.target,
            "palavra_chave": self.keyword,
            "descricao": self.label,
            "funcao_divine_pride": self.spec.function_id,
        }


# --- interpretação da necessidade ------------------------------------------

_RESIST_CUES = ("resist", "reduz", "reducao", "defesa contra", "protecao", "tanka", "aguentar")
_MAGIC_CUES = ("magic", "dano magico", "matk", "magia")
_DAMAGE_CUES = ("dano", "damage", "ataque", "atk", "matar", "bater", "hitar")
_COOLDOWN_CUES = ("recarga", "cooldown", "cd ")
_ELEMENT_HINTS = ("propriedade", "elemento", "element")
_SIZE_HINTS = ("tamanho", "size")
_RACE_HINTS = ("raca", "race")
_SKILL_HINTS = ("habilidade", "skill")
_ARTICLES = ("a", "o", "as", "os", "em", "de", "do", "da", "dos", "das", "contra", "no", "na", "para", "ao", "à")


def _lookup_term(text_norm: str, vocabulary: dict[str, Term]) -> tuple[str, Term] | None:
    """Acha o alvo mais longo do vocabulário presente no texto normalizado."""
    best: tuple[str, Term] | None = None
    best_len = 0
    padded = f" {text_norm} "
    for key, term in vocabulary.items():
        for form in (key, term.keyword, *term.aliases):
            form_norm = normalize(form)
            if f" {form_norm} " in padded and len(form_norm) > best_len:
                best, best_len = (key, term), len(form_norm)
    return best


def _resolve_target(kind: NeedKind, target: str) -> Need:
    spec = KIND_SPECS[kind]
    if spec.vocabulary is None:
        skill = target.strip().strip("[]").strip()
        if not skill:
            raise ValueError(f"Para {kind.value} informe o nome da habilidade, como aparece no LATAM.")
        return Need(kind, skill, skill)
    vocabulary = VOCABULARIES[spec.vocabulary]
    found = _lookup_term(normalize(target), vocabulary)
    if found is None:
        raise ValueError(
            f"Alvo {target!r} não reconhecido para {kind.value}. "
            f"Opções: {', '.join(vocabulary)}."
        )
    key, term = found
    return Need(kind, key, term.keyword)


def _strip_articles(words: Sequence[str]) -> list[str]:
    return [w for w in words if w not in _ARTICLES]


def parse_need(text: str, *, kind: NeedKind | str | None = None, target: str | None = None) -> Need:
    """Interpreta uma necessidade escrita livremente (ou já estruturada).

    Exemplos aceitos: "resistência a dragão", "dano em amorfo", "dano mágico
    contra fogo", "resistência a propriedade sombria", "dano em tamanho grande",
    "dano de [Sopro do Dragão]", "dano de Sopro do Dragão", "recarga de Esquife
    de Gelo", "FOR". Com `kind` (e opcionalmente `target`) não há adivinhação.
    """
    if kind is not None:
        try:
            kind_enum = NeedKind(kind)
        except ValueError as exc:
            raise ValueError(
                f"Tipo de necessidade desconhecido: {kind!r}. Opções: {', '.join(k.value for k in NeedKind)}."
            ) from exc
        return _resolve_target(kind_enum, target if target is not None else text)

    raw = (text or "").strip()
    if not raw:
        raise ValueError("Descreva a necessidade (ex.: 'resistência a dragão', 'dano em amorfo').")
    norm = normalize(raw.replace("+", " "))

    is_resist = any(cue in norm for cue in _RESIST_CUES)
    is_cooldown = any(cue in norm for cue in _COOLDOWN_CUES)
    is_magic = any(cue in norm for cue in _MAGIC_CUES)
    is_damage = any(cue in norm for cue in _DAMAGE_CUES) or is_magic

    # Habilidade entre colchetes ou anunciada ("habilidade X", "skill X").
    bracket = re.search(r"\[([^\]]+)\]", raw)
    skill_name: str | None = bracket.group(1).strip() if bracket else None
    if skill_name is None:
        for hint in _SKILL_HINTS:
            match = re.search(rf"\b{hint}\b\s*:?\s*(.+)$", raw, flags=re.IGNORECASE)
            if match:
                skill_name = match.group(1).strip()
                break

    wants_element = any(hint in norm for hint in _ELEMENT_HINTS)
    wants_size = any(hint in norm for hint in _SIZE_HINTS)
    wants_race = any(hint in norm for hint in _RACE_HINTS)

    target_kind: str | None = None
    found: tuple[str, Term] | None = None
    if skill_name is None:
        order: list[str]
        if wants_element:
            order = ["elemento", "raca", "tamanho"]
        elif wants_size:
            order = ["tamanho", "raca", "elemento"]
        elif wants_race:
            order = ["raca", "elemento", "tamanho"]
        else:
            order = ["raca", "elemento", "tamanho"]
        for vocab_name in order:
            found = _lookup_term(norm, VOCABULARIES[vocab_name])
            if found is not None:
                target_kind = vocab_name
                break

    if skill_name is None and found is None and not is_resist and not is_cooldown:
        # Sem alvo conhecido: pode ser atributo ("FOR", "+INT") ou habilidade ("dano de X").
        stat = _lookup_term(norm, STATS)
        if stat is not None and (not is_damage or len(norm.split()) <= 3):
            return Need(NeedKind.STAT, stat[0], stat[1].keyword)
        match = re.search(r"\b(?:dano|damage)\s+(?:d[eao]s?|from|of)\s+(.+)$", raw, flags=re.IGNORECASE)
        if match:
            skill_name = match.group(1).strip()

    if skill_name is None and found is None and is_cooldown:
        match = re.search(r"\b(?:recarga|cooldown|cd)\s+(?:d[eao]s?\s+)?(.+)$", raw, flags=re.IGNORECASE)
        if match:
            skill_name = match.group(1).strip()

    if skill_name is None and found is not None and is_damage and target_kind is not None:
        # "dano de sopro do dragão": o resto depois de "dano de" é mais que a raça → habilidade.
        match = re.search(r"\b(?:dano|damage)\s+d[eao]s?\s+(.+)$", raw, flags=re.IGNORECASE)
        if match:
            rest = _strip_articles(normalize(match.group(1)).split())
            key, term = found
            target_words = set()
            for form in (key, term.keyword, *term.aliases):
                target_words.update(normalize(form).split())
            if any(word not in target_words for word in rest):
                skill_name = match.group(1).strip()

    if skill_name is not None:
        skill_name = re.sub(r"^(a|o|de|do|da)\s+", "", skill_name.strip(), flags=re.IGNORECASE).strip(" .")
        kind_enum = NeedKind.SKILL_COOLDOWN if is_cooldown else NeedKind.SKILL_DAMAGE
        return _resolve_target(kind_enum, skill_name)

    if found is None:
        stats = ", ".join(term.keyword for term in STATS.values())
        raise ValueError(
            f"Não entendi a necessidade {raw!r}. Diga o tipo (resistência, dano, dano mágico, recarga) e o "
            f"alvo — raça ({', '.join(RACES)}), propriedade ({', '.join(ELEMENTS)}), tamanho "
            f"({', '.join(SIZES)}), habilidade entre colchetes ou atributo ({stats})."
        )

    key, term = found
    if is_resist:
        if target_kind == "elemento":
            return Need(NeedKind.RESIST_ELEMENT, key, term.keyword)
        if target_kind == "raca":
            return Need(NeedKind.RESIST_RACE, key, term.keyword)
        raise ValueError("Resistência só existe por raça ou por propriedade — não por tamanho.")

    if target_kind == "raca":
        return Need(NeedKind.MAGIC_DAMAGE_RACE if is_magic else NeedKind.DAMAGE_RACE, key, term.keyword)
    if target_kind == "elemento":
        return Need(NeedKind.MAGIC_DAMAGE_ELEMENT if is_magic else NeedKind.DAMAGE_ELEMENT, key, term.keyword)
    return Need(NeedKind.MAGIC_DAMAGE_SIZE if is_magic else NeedKind.DAMAGE_SIZE, key, term.keyword)


def vocabulary() -> dict[str, Any]:
    """Tipos e alvos aceitos — para a CLI (`--help`) e para o modelo no MCP."""
    return {
        "tipos": {
            spec.kind.value: {
                "descricao": spec.label.format(alvo="X"),
                "alvo": spec.vocabulary or "nome da habilidade (como aparece no LATAM)",
                "funcao_divine_pride": spec.function_id,
            }
            for spec in KIND_SPECS.values()
        },
        "alvos": {
            name: {key: term.keyword for key, term in vocab.items()} for name, vocab in VOCABULARIES.items()
        },
        "exemplos": [
            "resistência a dragão",
            "dano em amorfo",
            "dano mágico contra propriedade fogo",
            "dano em tamanho grande",
            "dano de [Sopro do Dragão]",
            "recarga de [Esquife de Gelo]",
        ],
    }


# --- leitura da descrição -----------------------------------------------------

_SEPARATOR_RE = re.compile(r"^[-_=—–\s]{3,}$")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_VALUE_RE = re.compile(r"([+-])\s?(\d+(?:[.,]\d+)?)\s*(%?)")
_BRACKET_RE = re.compile(r"\[([^\]]+)\]")


@dataclass(frozen=True)
class EffectMatch:
    """Uma linha da descrição que fala da necessidade."""

    text: str
    context: str | None  # condição que precede a linha ("Refino +7 ou mais:", "Conjunto: ...")
    in_set: bool
    value: float | None
    percent: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "texto": self.text,
            "contexto": self.context,
            "conjunto": self.in_set,
            "valor": self.value,
            "percentual": self.percent,
        }


def description_lines(description: str) -> list[str]:
    """Linhas limpas de uma descrição (aceita `\\n` ou `<br>`)."""
    text = _BR_RE.sub("\n", description or "")
    text = _TAG_RE.sub("", text)
    return [line.strip() for line in text.replace("\r", "").split("\n")]


def _line_mentions(line_norm: str, line_raw: str, need: Need) -> bool:
    keyword_norm = normalize(need.keyword)
    spec = need.spec
    if spec.bracketed:
        return any(keyword_norm in normalize(name) for name in _BRACKET_RE.findall(line_raw))
    if spec.kind is NeedKind.STAT:
        return re.search(rf"\b{re.escape(need.keyword)}\b", line_raw) is not None and "+" in line_raw
    return f" {keyword_norm} " in f" {line_norm} " or f" {keyword_norm}." in f" {line_norm} "


def match_lines(description: str, need: Need) -> list[EffectMatch]:
    """Linhas da descrição que atendem à necessidade, com condição e valor."""
    spec = need.spec
    matches: list[EffectMatch] = []
    context: str | None = None
    set_items: list[str] = []
    in_set = False

    for line in description_lines(description):
        if not line:
            continue
        if _SEPARATOR_RE.match(line):
            context, set_items, in_set = None, [], False
            continue
        norm = normalize(line)
        if norm.startswith("conjunto"):
            in_set, set_items, context = True, [], None
            continue
        if in_set and line.startswith("[") and context is None and not any(c in norm for c in spec.cues):
            set_items.append(re.sub(r"\s+(e|ou)$", "", line.strip(" ,")))
            continue
        if line.endswith(":"):
            context = line
            continue

        if not _line_mentions(norm, line, need):
            continue
        if spec.cues and not any(cue in norm for cue in spec.cues):
            continue
        if any(bad in norm for bad in spec.excludes):
            continue

        value: float | None = None
        percent = False
        found = _VALUE_RE.search(line)
        if found:
            sign, number, pct = found.groups()
            value = float(number.replace(",", "."))
            if sign == "-":
                value = -value
            percent = pct == "%"

        shown_context = context
        if in_set and set_items:
            conjunto = "Conjunto com " + ", ".join(set_items)
            shown_context = f"{conjunto} — {context}" if context else conjunto
        matches.append(EffectMatch(line, shown_context, in_set, value, percent))
    return matches


# --- orquestração -------------------------------------------------------------

#: Categorias da listagem aceitas, com os nomes em português.
CATEGORY_ALIASES: dict[str, str] = {
    "arma": "weapon", "armas": "weapon", "weapon": "weapon", "weapons": "weapon",
    "armadura": "armor", "armaduras": "armor", "armor": "armor", "equipamento": "armor",
    "carta": "card", "cartas": "card", "card": "card", "cards": "card",
    "sombra": "shadow", "sombrio": "shadow", "shadow": "shadow",
}
DEFAULT_CATEGORIES: tuple[str, ...] = ("armor", "weapon")

#: Subtipos da listagem (nomes do site) e apelidos em português.
SUBTYPE_ALIASES: dict[str, str] = {
    "headgear": "Headgear", "chapeu": "Headgear", "cabeca": "Headgear", "elmo": "Headgear",
    "armor": "Armor", "armadura": "Armor", "corpo": "Armor",
    "shield": "Shield", "escudo": "Shield",
    "garment": "Garment", "capa": "Garment", "manto": "Garment",
    "shoes": "Shoes", "sapato": "Shoes", "bota": "Shoes", "botas": "Shoes", "calcado": "Shoes",
    "accessory": "Accessory", "acessorio": "Accessory", "anel": "Accessory",
    "dagger": "Dagger", "adaga": "Dagger",
    "sword": "Sword", "espada": "Sword", "espada de uma mao": "Sword",
    "two-handed sword": "Two-handed Sword", "espada de duas maos": "Two-handed Sword", "bastarda": "Two-handed Sword",
    "spear": "Spear", "lanca": "Spear", "lanca de uma mao": "Spear",
    "two-handed spear": "Two-handed Spear", "lanca de duas maos": "Two-handed Spear",
    "axe": "Axe", "machado": "Axe",
    "two-handed axe": "Two-handed Axe", "machado de duas maos": "Two-handed Axe",
    "mace": "Mace", "maca": "Mace", "clava": "Mace",
    "two-handed mace": "Two-handed Mace", "maca de duas maos": "Two-handed Mace",
    "rod": "Rod", "cajado": "Rod", "bastao": "Rod",
    "two-handed rod": "Two-handed Rod", "cajado de duas maos": "Two-handed Rod",
    "bow": "Bow", "arco": "Bow",
    "knuckle": "Knuckle", "soco": "Knuckle", "soqueira": "Knuckle",
    "musical instrument": "Musical Instrument", "instrumento": "Musical Instrument",
    "whip": "Whip", "chicote": "Whip",
    "book": "Book", "livro": "Book",
    "katar": "Katar",
    "pistol": "Pistol", "pistola": "Pistol", "revolver": "Pistol",
    "rifle": "Rifle",
    "gatling gun": "Gatling Gun", "metralhadora": "Gatling Gun",
    "shotgun": "Shotgun", "escopeta": "Shotgun",
    "grenade launcher": "Grenade Launcher", "lancador de granadas": "Grenade Launcher",
    "huuma shuriken": "Huuma Shuriken", "shuriken": "Huuma Shuriken",
}

#: IDs de classe do Divine Pride (filtro `jobGroups`) por chave do rAthena.
DP_JOB_IDS: dict[str, int] = {
    "Novice": 0, "Super_Novice": 23, "Super_Novice_E": 4190, "Hyper_Novice": 4307,
    "Swordman": 1, "Knight": 7, "Crusader": 14, "Swordman_High": 4002, "Lord_Knight": 4008,
    "Paladin": 4015, "Rune_Knight": 4054, "Royal_Guard": 4066, "Dragon_Knight": 4252, "Imperial_Guard": 4258,
    "Mage": 2, "Wizard": 9, "Sage": 16, "Mage_High": 4003, "High_Wizard": 4010, "Professor": 4017,
    "Warlock": 4055, "Sorcerer": 4067, "Arch_Mage": 4255, "Elemental_Master": 4261,
    "Archer": 3, "Hunter": 11, "Bard": 19, "Dancer": 20, "Archer_High": 4004, "Sniper": 4012,
    "Clown": 4020, "Gypsy": 4021, "Ranger": 4056, "Minstrel": 4068, "Wanderer": 4069,
    "Windhawk": 4257, "Troubadour": 4263, "Trouvere": 4264,
    "Acolyte": 4, "Priest": 8, "Monk": 15, "Acolyte_High": 4005, "High_Priest": 4009, "Champion": 4016,
    "Arch_Bishop": 4057, "Sura": 4070, "Cardinal": 4256, "Inquisitor": 4262,
    "Merchant": 5, "Blacksmith": 10, "Alchemist": 18, "Merchant_High": 4006, "Whitesmith": 4011,
    "Creator": 4019, "Mechanic": 4058, "Genetic": 4071, "Meister": 4253, "Biolo": 4259,
    "Thief": 6, "Assassin": 12, "Rogue": 17, "Thief_High": 4007, "Assassin_Cross": 4013, "Stalker": 4018,
    "Guillotine_Cross": 4059, "Shadow_Chaser": 4072, "Shadow_Cross": 4254, "Abyss_Chaser": 4260,
    "Taekwon": 4046, "Star_Gladiator": 4047, "Soul_Linker": 4049, "Star_Emperor": 4239, "Soul_Reaper": 4240,
    "Sky_Emperor": 4302, "Soul_Ascetic": 4303,
    "Ninja": 25, "Kagerou": 4211, "Oboro": 4212, "Shiranui": 4305, "Shinkiro": 4304,
    "Gunslinger": 24, "Rebellion": 4215, "Night_Watch": 4306,
    "Summoner": 4218, "Spirit_Handler": 4308,
}


def resolve_categories(names: Iterable[str] | None) -> tuple[str, ...]:
    """'arma'/'armadura'/'carta'/'sombra' (ou os nomes do site) → categorias da listagem."""
    if not names:
        return DEFAULT_CATEGORIES
    out: list[str] = []
    for name in names:
        key = normalize(name)
        if key not in CATEGORY_ALIASES:
            raise ValueError(f"Categoria desconhecida: {name!r}. Use arma, armadura, carta ou sombra.")
        category = CATEGORY_ALIASES[key]
        if category not in out:
            out.append(category)
    return tuple(out)


def resolve_sub_types(names: Iterable[str] | None) -> tuple[str, ...]:
    out: list[str] = []
    for name in names or ():
        key = normalize(name)
        sub_type = SUBTYPE_ALIASES.get(key)
        if sub_type is None:
            # Aceita o nome do site tal como está ("Accessory", "Two-handed Sword").
            sub_type = name.strip()
        if sub_type not in out:
            out.append(sub_type)
    return tuple(out)


def resolve_job_ids(names: Iterable[str] | None) -> tuple[int, ...]:
    out: list[int] = []
    for name in names or ():
        key = canonical_job_key(name, known=set(DP_JOB_IDS))
        if key is None:
            raise ValueError(f"Classe desconhecida: {name!r}.")
        job_id = DP_JOB_IDS[key]
        if job_id not in out:
            out.append(job_id)
    return tuple(out)


@dataclass
class SearchOptions:
    categories: tuple[str, ...] = DEFAULT_CATEGORIES
    sub_types: tuple[str, ...] = ()
    job_ids: tuple[int, ...] = ()
    min_level: int | None = None
    max_level: int | None = None
    min_slots: int | None = None
    max_pages: int = 3  # páginas da listagem por categoria (20 itens cada)
    max_details: int = 25  # quantos candidatos consultar na API (1 req/s)
    details: bool = True


def _row_dict(row: ItemRow, category: str) -> dict[str, Any]:
    data = row.to_dict()
    data["categoria"] = category
    return data


def find_equipment(client: DivinePrideClient, need: Need, options: SearchOptions | None = None) -> dict[str, Any]:
    """Busca itens do LATAM que atendem à necessidade. Veja o módulo para as regras."""
    opts = options or SearchOptions()
    spec = need.spec
    notas: list[str] = []
    excluidos = {"sem_nome_latam": 0, "fora_do_latam": 0, "sem_slots_suficientes": 0}
    candidates: dict[int, tuple[str, ItemRow]] = {}
    truncated = False

    for category in opts.categories:
        page = 1
        while True:
            listing = client.list_items(
                category,
                function_id=spec.function_id,
                description=need.keyword,
                sub_types=opts.sub_types,
                job_ids=opts.job_ids,
                min_level=opts.min_level,
                max_level=opts.max_level,
                page=page,
                language="pt",
            )
            if listing.region and listing.region.casefold() != LATAM.casefold():
                raise SourceError(
                    f"O site do Divine Pride serviu a base {listing.region!r} em vez de LATAM; "
                    "a busca por necessidade só funciona na base LATAM em português."
                )
            for row in listing.rows:
                if not row.on_latam:
                    excluidos["fora_do_latam"] += 1
                    continue
                if not row.has_name:
                    excluidos["sem_nome_latam"] += 1
                    continue
                if opts.min_slots is not None and (row.slots or 0) < opts.min_slots:
                    excluidos["sem_slots_suficientes"] += 1
                    continue
                candidates.setdefault(row.id, (category, row))
            if page >= listing.pages:
                break
            if page >= opts.max_pages:
                truncated = True
                break
            page += 1

    if truncated:
        notas.append(
            f"A listagem tinha mais páginas do que o limite ({opts.max_pages} por categoria); "
            "refine com subtipo, classe ou nível para ver o restante."
        )

    ordered = list(candidates.items())
    to_fetch = ordered[: opts.max_details] if opts.details else []
    remaining = ordered[len(to_fetch):]

    itens: list[dict[str, Any]] = []
    possiveis: list[dict[str, Any]] = []
    for item_id, (category, row) in to_fetch:
        try:
            data = client.item(item_id, server=LATAM, language="pt")
        except (NotFound, WrongRegion):
            # 404 ou dados de outra região: o item não está na base LATAM.
            excluidos["fora_do_latam"] += 1
            continue
        name = str(data.get("name") or "").strip() or row.name
        description = str(data.get("description") or "")
        matches = match_lines(description, need)
        entry = _row_dict(row, category)
        entry["nome"] = name
        if data.get("slots") is not None:
            entry["slots"] = data.get("slots")
        if data.get("required_level") is not None:
            entry["nivel_necessario"] = data.get("required_level")
        entry["efeitos"] = [m.to_dict() for m in matches]
        values = [abs(m.value) for m in matches if m.value is not None]
        entry["melhor_valor"] = max(values) if values else None
        entry["soma_valores"] = sum(values) if values else None
        if matches:
            itens.append(entry)
        else:
            entry["descricao"] = description
            possiveis.append(entry)

    itens.sort(key=lambda e: (-(e["melhor_valor"] or 0), -(e["soma_valores"] or 0), e["nome"]))

    if not opts.details:
        notas.append("Detalhes desligados: a lista veio só da listagem do site (sem as linhas de efeito).")
    elif remaining:
        notas.append(
            f"{len(remaining)} candidatos não foram consultados na API (limite de {opts.max_details}); "
            "estão em `nao_consultados`."
        )
    if possiveis:
        notas.append(
            "`possiveis` são itens que o Divine Pride classificou com essa função, mas cuja descrição "
            "não traz uma linha reconhecível — vale ler a descrição."
        )
    if excluidos["sem_nome_latam"]:
        notas.append(
            f"{excluidos['sem_nome_latam']} itens aparecem na base LATAM sem nome em português "
            "(provavelmente não lançados) e foram descartados."
        )

    return {
        "necessidade": need.to_dict(),
        "servidor": LATAM,
        "categorias": list(opts.categories),
        "filtros": {
            "subtipos": list(opts.sub_types),
            "classes_divine_pride": list(opts.job_ids),
            "nivel_min": opts.min_level,
            "nivel_max": opts.max_level,
            "slots_min": opts.min_slots,
        },
        "candidatos": len(candidates),
        "consultados": len(to_fetch),
        "itens": itens,
        "possiveis": possiveis,
        "nao_consultados": [_row_dict(row, category) for _, (category, row) in remaining],
        "excluidos": excluidos,
        "notas": notas,
    }
