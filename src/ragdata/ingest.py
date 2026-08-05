"""Ingestão de personagem: YAML/JSON, texto colado e leitura de prints.

Sobre os prints: o ragdata **não** faz OCR. Quem enxerga a imagem é o modelo
(pelo servidor MCP), e a função aqui é dizer com precisão o que extrair e em que
formato — `screenshot_instructions()` devolve exatamente isso. O JSON produzido
entra em `character_from_payload()`, que valida tudo.
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml
from pydantic import ValidationError

from .errors import RagdataError
from .models import Character, EquipSlot, Goal, WeaponType

#: Rótulos que aparecem na janela de status em PT-BR e em inglês.
_STAT_PATTERNS: dict[str, tuple[str, ...]] = {
    "str": ("str", "for", "forca", "força"),
    "agi": ("agi", "agilidade"),
    "vit": ("vit", "vitalidade"),
    "int": ("int", "inteligencia", "inteligência"),
    "dex": ("dex", "des", "destreza"),
    "luk": ("luk", "sor", "sorte"),
}

_LEVEL_PATTERNS = {
    "base_level": ("base level", "nivel base", "nível base", "base lv", "lv base"),
    "job_level": ("job level", "nivel de classe", "nível de classe", "job lv", "classe lv"),
}


def character_from_payload(payload: dict[str, Any]) -> Character:
    """Valida um dicionário (vindo de JSON, YAML ou da leitura de prints)."""
    try:
        return Character.model_validate(payload)
    except ValidationError as exc:
        raise RagdataError(f"Personagem inválido:\n{_format_validation_error(exc)}") from exc


def _format_validation_error(exc: ValidationError) -> str:
    linhas = []
    for erro in exc.errors():
        caminho = ".".join(str(p) for p in erro["loc"]) or "(raiz)"
        linhas.append(f"  - {caminho}: {erro['msg']}")
    return "\n".join(linhas)


def load_character_file(path: str) -> Character:
    """Lê um personagem de arquivo YAML ou JSON."""
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    return load_character_text(content)


def load_character_text(content: str) -> Character:
    """Lê um personagem de texto YAML ou JSON."""
    content = content.strip()
    if not content:
        raise RagdataError("Conteúdo vazio.")
    try:
        payload = json.loads(content) if content.startswith("{") else yaml.safe_load(content)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise RagdataError(f"Não consegui interpretar como YAML/JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RagdataError("O conteúdo precisa ser um mapa com os campos do personagem.")
    return character_from_payload(payload)


def parse_freeform(text: str) -> dict[str, Any]:
    """Extrai o que der de uma descrição solta, em PT-BR ou inglês.

    Pega classe, níveis e os seis atributos de coisas como::

        Rune Knight base 175 job 60
        STR 120 AGI 90 VIT 80 INT 40 DEX 90 LUK 30

    É um atalho para conversa; o que ele não achar simplesmente não aparece no
    resultado — nada é adivinhado. Equipamentos não são extraídos aqui.
    """
    normalizado = _strip_accents(text).casefold()
    out: dict[str, Any] = {}

    stats: dict[str, int] = {}
    for key, rotulos in _STAT_PATTERNS.items():
        for rotulo in rotulos:
            match = re.search(rf"\b{re.escape(rotulo)}\b\s*[:=]?\s*(\d{{1,3}})", normalizado)
            if match:
                stats[key] = int(match.group(1))
                break
    if stats:
        out["stats"] = stats

    for campo, rotulos in _LEVEL_PATTERNS.items():
        for rotulo in rotulos:
            match = re.search(rf"{re.escape(rotulo)}\s*[:=]?\s*(\d{{1,3}})", normalizado)
            if match:
                out[campo] = int(match.group(1))
                break

    # "base 175 job 60" — forma curta e muito comum.
    if "base_level" not in out:
        match = re.search(r"\bbase\s*[:=]?\s*(\d{1,3})\b", normalizado)
        if match:
            out["base_level"] = int(match.group(1))
    if "job_level" not in out:
        match = re.search(r"\bjob\s*[:=]?\s*(\d{1,3})\b", normalizado)
        if match:
            out["job_level"] = int(match.group(1))

    for goal in Goal:
        if goal.value in normalizado:
            out["goal"] = goal.value
            break

    job = _guess_job(text)
    if job:
        out["job"] = job

    return out


def _guess_job(text: str) -> str | None:
    """Procura um nome de classe conhecido no texto (PT-BR ou inglês)."""
    from .errors import GameDataMissing
    from .jobs import JOB_ALIASES, normalize

    candidatos = dict.fromkeys(JOB_ALIASES)
    try:  # os nomes em inglês só existem se as tabelas já foram baixadas
        from .gamedata import job_index

        candidatos.update({normalize(key): None for key in job_index()})
    except GameDataMissing:
        pass

    normalizado = normalize(text)
    # Nomes mais longos primeiro, para "cavaleiro runico" vencer "cavaleiro".
    for nome in sorted(candidatos, key=len, reverse=True):
        if re.search(rf"\b{re.escape(nome)}\b", normalizado):
            return nome
    return None


def _strip_accents(text: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def character_schema() -> dict[str, Any]:
    """Schema JSON do personagem, para quem for montar o payload."""
    return Character.model_json_schema()


def screenshot_instructions() -> str:
    """Instruções de leitura de print, para o modelo que enxerga a imagem.

    Devolvidas pela ferramenta MCP `schema_do_personagem`. São deliberadamente
    específicas sobre a armadilha mais comum: a janela de status mostra
    `base + bônus`, e o motor precisa do **base**.
    """
    slots = ", ".join(s.value for s in EquipSlot)
    armas = ", ".join(w.value for w in WeaponType)
    objetivos = ", ".join(g.value for g in Goal)
    return f"""
Como transformar prints de Ragnarok em um personagem para o ragdata.

REGRA MAIS IMPORTANTE — atributos base vs. totais
    Na janela de status cada atributo aparece como dois números, por exemplo
    "STR 90 + 35". O primeiro é o **base** (o que o jogador distribuiu) e o
    segundo é a soma de bônus de classe e equipamento.
    Preencha `stats` **somente com o número da esquerda**. O ragdata recalcula
    os bônus sozinho — se você somar os dois, a auditoria de pontos vai acusar
    build impossível.
    Se o print mostrar só um número por atributo, diga isso na resposta em vez
    de adivinhar qual dos dois é.

O QUE EXTRAIR
    - `job`: a classe, como aparece no jogo (PT-BR serve: "Cavaleiro Rúnico").
    - `base_level` e `job_level`.
    - `stats`: str, agi, vit, int, dex, luk — valores base.
    - `traits` (só 4ª classe): pow, sta, wis, spl, con, crt.
    - `equipment`: uma entrada por peça equipada, com:
        * `slot`: um de [{slots}]
        * `name`: o nome como aparece
        * `refine`: o número após o "+" no nome do item (0 se não houver)
        * `slots`: quantidade de espaços de carta, se der para ver
        * `cards`: as cartas encaixadas, cada uma com `name` e, quando o efeito
          for condicional ou por raça/elemento, um `note` em texto
        * para armas: `weapon_type` (um de [{armas}]), `weapon_level` e `base_atk`
    - `skills`: as habilidades relevantes com `name` e `level`, se visíveis.
    - `goal`: um de [{objetivos}], se o jogador tiver dito o objetivo.

O QUE NÃO INVENTAR
    - Não estime ATK/MATK/DEF do print: o ragdata calcula. Se você informar
      `base_atk` de uma arma, use o valor do item no Divine Pride, não o ATK
      total da janela de status.
    - Efeito de carta que não vira número (por raça, condicional, por skill)
      vai em `note`, não em `bonuses`.
    - Campo que você não conseguiu ler: omita e avise. Um valor chutado
      contamina toda a análise.

DEPOIS DE MONTAR
    Chame `analisar_personagem` com esse JSON. Se o jogador citou um alvo
    ("quero matar o Baphomet"), busque o monstro com `buscar_monstro` e passe o
    id junto.
""".strip()


__all__ = [
    "character_from_payload",
    "character_schema",
    "load_character_file",
    "load_character_text",
    "parse_freeform",
    "screenshot_instructions",
]
