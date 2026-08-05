"""Resolução de nomes de classe: PT-BR (bRO/LATAM) → chave do rAthena.

O jogador digita "Cavaleiro Rúnico" ou manda um print onde aparece "Arcebispo".
Aqui traduzimos isso para a chave usada nas tabelas (`Rune_Knight`,
`Arch_Bishop`). Nomes em inglês e as próprias chaves também são aceitos.
"""

from __future__ import annotations

import difflib
import unicodedata

#: Apelidos em PT-BR e variações comuns → chave do rAthena.
#: Uma chave pode ter vários apelidos; a busca é feita sem acento e sem caixa.
JOB_ALIASES: dict[str, str] = {
    # --- Classes iniciais ---
    "aprendiz": "Novice",
    "novico": "Acolyte",  # bRO chama Acolyte de "Noviço"
    "acolito": "Acolyte",
    "espadachim": "Swordman",
    "mago": "Mage",
    "arqueiro": "Archer",
    "mercador": "Merchant",
    "gatuno": "Thief",
    "super aprendiz": "Super_Novice",
    "supernovice": "Super_Novice",
    # --- Segunda classe ---
    "cavaleiro": "Knight",
    "sacerdote": "Priest",
    "bruxo": "Wizard",
    "ferreiro": "Blacksmith",
    "cacador": "Hunter",
    "assassino": "Assassin",
    "templario": "Crusader",
    "cruzado": "Crusader",
    "monge": "Monk",
    "sabio": "Sage",
    "arruaceiro": "Rogue",
    "alquimista": "Alchemist",
    "bardo": "Bard",
    "odalisca": "Dancer",
    "justiceiro": "Gunslinger",
    "ninja": "Ninja",
    "taekwon": "Taekwon",
    "justiceiro estelar": "Star_Gladiator",
    "espiritualista": "Soul_Linker",
    # --- Transclasses ---
    "lorde": "Lord_Knight",
    "lord knight": "Lord_Knight",
    "sumo sacerdote": "High_Priest",
    "arquimago": "High_Wizard",
    "mestre ferreiro": "Whitesmith",
    "sniper": "Sniper",
    "assassino cruel": "Assassin_Cross",
    "paladino": "Paladin",
    "campeao": "Champion",
    "professor": "Professor",
    "stalker": "Stalker",
    "criador": "Creator",
    "menestrel": "Clown",
    "cigana": "Gypsy",
    # --- Terceira classe ---
    "cavaleiro runico": "Rune_Knight",
    "runico": "Rune_Knight",
    "feiticeiro": "Sorcerer",
    "arcebispo": "Arch_Bishop",
    "mecanico": "Mechanic",
    "cruz da guilhotina": "Guillotine_Cross",
    "guarda real": "Royal_Guard",
    "trovador": "Minstrel",
    "maestro": "Minstrel",
    "andarilha": "Wanderer",
    "sura": "Sura",
    "geneticista": "Genetic",
    "sombra sinistra": "Shadow_Chaser",
    "renegado": "Shadow_Chaser",
    "ranger": "Ranger",
    "warlock": "Warlock",
    "rebelde": "Rebellion",
    "invocador": "Summoner",
    "doram": "Summoner",
    "imperador estelar": "Star_Emperor",
    "ceifador de almas": "Soul_Reaper",
    "kagerou": "Kagerou",
    "oboro": "Oboro",
    # --- Quarta classe ---
    "cavaleiro dragao": "Dragon_Knight",
    "dragon knight": "Dragon_Knight",
    "guarda imperial": "Imperial_Guard",
    "mestre": "Meister",
    "meister": "Meister",
    "cruz sombria": "Shadow_Cross",
    "abismal": "Abyss_Chaser",
    "cacador do abismo": "Abyss_Chaser",
    "grao mago": "Arch_Mage",
    "arquimago supremo": "Arch_Mage",
    "mestre elemental": "Elemental_Master",
    "cardeal": "Cardinal",
    "inquisidor": "Inquisitor",
    "falcao do vento": "Windhawk",
    "trovador supremo": "Troubadour",
    "trouvere": "Trouvere",
    "imperador celeste": "Sky_Emperor",
    "asceta espiritual": "Soul_Ascetic",
    "hiper aprendiz": "Hyper_Novice",
    "guardiao espiritual": "Spirit_Handler",
    "vigia noturno": "Night_Watch",
    "biologo": "Biolo",
    "biolo": "Biolo",
}

#: Sufixos que marcam a versão transcendente de uma terceira classe.
_TRANS_MARKERS = ("transclasse", "trans", "_t")


def normalize(text: str) -> str:
    """Minúsculas, sem acento, com espaços e underscores unificados."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = stripped.replace("_", " ").replace("-", " ").strip().casefold()
    return " ".join(cleaned.split())


def _key_forms(known: set[str]) -> dict[str, str]:
    """Formas normalizadas das chaves do rAthena → chave original."""
    return {normalize(key): key for key in known}


def canonical_job_key(name: str, *, known: set[str]) -> str | None:
    """Devolve a chave do rAthena para `name`, ou None se não reconhecer.

    `known` são as chaves realmente presentes nas tabelas — assim um apelido que
    aponte para uma classe inexistente naquela versão simplesmente não resolve.
    """
    if not name or not name.strip():
        return None

    raw = normalize(name)
    forms = _key_forms(known)

    # 1) A própria chave ou o nome em inglês.
    if raw in forms:
        return forms[raw]

    # 2) Apelido direto em PT-BR.
    if raw in JOB_ALIASES and JOB_ALIASES[raw] in known:
        return JOB_ALIASES[raw]

    # 3) Variante transcendente: "arcebispo trans", "Rune Knight T".
    for marker in _TRANS_MARKERS:
        if raw.endswith(" " + normalize(marker)) or raw.endswith(normalize(marker)):
            base = raw[: -len(normalize(marker))].strip()
            base_key = canonical_job_key(base, known=known) if base else None
            if base_key and f"{base_key}_T" in known:
                return f"{base_key}_T"
            if base_key:
                return base_key

    return None


def suggest_jobs(name: str, *, known: set[str], limit: int = 3) -> list[str]:
    """Sugestões de classe para uma entrada não reconhecida."""
    candidates = {normalize(k): k for k in known}
    candidates.update({alias: JOB_ALIASES[alias] for alias in JOB_ALIASES})
    matches = difflib.get_close_matches(normalize(name), list(candidates), n=limit, cutoff=0.6)
    seen: list[str] = []
    for match in matches:
        key = candidates[match]
        if key in known and key not in seen:
            seen.append(key)
    return seen


def display_name(key: str) -> str:
    """Nome PT-BR mais comum para uma chave, quando existir apelido."""
    for alias, target in JOB_ALIASES.items():
        if target == key:
            return alias.title()
    return key.replace("_", " ")
