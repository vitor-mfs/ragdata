"""Servidor MCP do ragdata.

É o modo recomendado de uso: o modelo enxerga os prints, monta o JSON do
personagem seguindo `schema_do_personagem` e chama as ferramentas de análise.
Aqui não há visão nem OCR — só cálculo, dados e validação.

Instalação no Claude Code::

    claude mcp add ragdata -- ragdata-mcp
"""

from __future__ import annotations

from typing import Any

from . import analysis, ingest, needs
from .config import get_settings
from .engine import compute
from .errors import RagdataError
from .models import Goal
from .setup_data import missing_tables
from .sources import BrowikiClient, DivinePrideClient

try:  # SDK novo
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - SDKs anteriores
    try:
        from mcp.server.fastmcp import FastMCP as _Server
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "O servidor MCP precisa do pacote `mcp`. Instale com: pip install 'ragdata[mcp]'"
        ) from exc

INSTRUCTIONS = """
Auxiliar de build de Ragnarok Online Renewal (servidor LATAM).

Fluxo esperado quando alguém manda prints ou descreve um personagem:

1. Chame `schema_do_personagem` e siga as instruções — em especial a distinção
   entre atributo **base** e **total**, que é o erro mais comum na leitura.
2. Monte o JSON do personagem a partir do que você viu ou leu.
3. Se houver um alvo declarado ("quero matar o MVP X"), busque com
   `buscar_monstro` e passe o `monster_id`.
4. Chame `analisar_personagem`.
5. Para responder "e se eu mudar X?", use `simular_mudanca`.
6. Para "que arma/armadura me dá resistência a Dragão / dano em Amorfo / dano de
   tal habilidade?", use `buscar_equipamento_por_necessidade` — ela só devolve
   itens listados na base LATAM do Divine Pride e mostra a linha exata da
   descrição que atende ao pedido. Prefira `tipo` + `alvo` estruturados; o nome
   de habilidade deve ser o do cliente LATAM em português ("Sopro do Dragão").

Os números saem das fórmulas Renewal do rAthena. Efeitos de carta em texto e
dano de habilidade contra alvo não são calculados — quando forem decisivos,
diga isso em vez de estimar.
""".strip()

server = _Server(name="ragdata", instructions=INSTRUCTIONS)


def _erro(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "erro": str(exc)}


def _checar_tabelas() -> dict[str, Any] | None:
    faltando = missing_tables()
    if faltando:
        return {
            "ok": False,
            "erro": (
                f"Tabelas de jogo ausentes ({', '.join(faltando)}). "
                "Rode `ragdata setup` no terminal, com acesso à internet."
            ),
        }
    return None


@server.tool(
    name="schema_do_personagem",
    description=(
        "Schema JSON do personagem e instruções de como extrair os dados de prints "
        "da janela de status e de equipamentos. Chame isto ANTES de montar o personagem."
    ),
)
def schema_do_personagem() -> dict[str, Any]:
    return {
        "ok": True,
        "instrucoes": ingest.screenshot_instructions(),
        "schema": ingest.character_schema(),
    }


@server.tool(
    name="analisar_personagem",
    description=(
        "Analisa uma build: stats derivados, contabilidade de pontos, breakpoints "
        "e sugestões priorizadas pelo objetivo. `personagem` segue o schema de "
        "`schema_do_personagem`. `objetivo` é um de: leveling, farm, mvp, pvp, woe, "
        "tank, support. `monster_id` é opcional e traz comparações contra o alvo."
    ),
)
def analisar_personagem(
    personagem: dict[str, Any],
    objetivo: str | None = None,
    monster_id: int | None = None,
) -> dict[str, Any]:
    if (erro := _checar_tabelas()) is not None:
        return erro
    try:
        character = ingest.character_from_payload(personagem)
        goal = Goal(objetivo) if objetivo else None
        target = None
        if monster_id is not None:
            with DivinePrideClient() as client:
                target = client.monster_target(monster_id)
        report = analysis.analyze(character, goal=goal, target=target)
    except ValueError as exc:
        return _erro(exc)
    except RagdataError as exc:
        return _erro(exc)
    return {"ok": True, **report.to_dict()}


@server.tool(
    name="calcular_stats",
    description="Só os stats derivados de um personagem, sem sugestões.",
)
def calcular_stats(personagem: dict[str, Any]) -> dict[str, Any]:
    if (erro := _checar_tabelas()) is not None:
        return erro
    try:
        character = ingest.character_from_payload(personagem)
        derived = compute(character)
    except RagdataError as exc:
        return _erro(exc)
    return {
        "ok": True,
        "classe": derived.job_key,
        "atk": derived.atk_display,
        "matk": derived.matk_display,
        "hit": derived.hit,
        "flee": derived.flee,
        "crit": derived.crit,
        "def": derived.def_display,
        "mdef": derived.mdef_display,
        "aspd": derived.aspd,
        "aspd_teto": derived.aspd_cap,
        "ataques_por_segundo": derived.attacks_per_second,
        "hp_max": derived.max_hp,
        "sp_max": derived.max_sp,
        "patk": derived.patk,
        "smatk": derived.smatk,
        "res": derived.res,
        "mres": derived.mres,
        "cast_instantaneo": derived.instant_cast,
        "reducao_cast_variavel": derived.variable_cast_total_reduction,
        "stats_totais": derived.total_stats,
        "avisos": derived.warnings,
    }


@server.tool(
    name="simular_mudanca",
    description=(
        "Compara a build atual com uma alteração e mostra o que muda em cada número. "
        'As mudanças aceitam {"stats": {...}}, {"traits": {...}}, '
        '{"refine": {"nome do item": 10}}, "base_level" e "job_level".'
    ),
)
def simular_mudanca(personagem: dict[str, Any], mudancas: dict[str, Any]) -> dict[str, Any]:
    if (erro := _checar_tabelas()) is not None:
        return erro
    try:
        character = ingest.character_from_payload(personagem)
        resultado = analysis.simulate(character, mudancas)
    except (RagdataError, ValueError) as exc:
        return _erro(exc)
    return {"ok": True, **resultado}


@server.tool(
    name="ler_texto_de_personagem",
    description=(
        "Extrai classe, níveis e atributos de uma descrição solta em português ou "
        "inglês. Devolve um rascunho parcial — complete os equipamentos antes de analisar."
    ),
)
def ler_texto_de_personagem(texto: str) -> dict[str, Any]:
    return {"ok": True, "rascunho": ingest.parse_freeform(texto)}


@server.tool(
    name="buscar_item",
    description=(
        "Busca um item no Divine Pride. Informe `item_id` para a consulta exata "
        "(via API) ou `nome` para uma busca por texto (melhor-esforço, lê a página "
        "de busca do site). Respeita o limite de 1 requisição por segundo."
    ),
)
def buscar_item(item_id: int | None = None, nome: str | None = None) -> dict[str, Any]:
    if item_id is None and not nome:
        return {"ok": False, "erro": "Informe `item_id` ou `nome`."}
    try:
        with DivinePrideClient() as client:
            if item_id is not None:
                data = client.item(item_id)
                data.pop("raw", None)
                return {"ok": True, "item": data}
            resultados = client.search(nome or "", kind="item")
    except RagdataError as exc:
        return _erro(exc)
    return {
        "ok": True,
        "resultados": resultados,
        "nota": "Busca por nome é melhor-esforço; use `item_id` para dados completos.",
    }


@server.tool(
    name="buscar_monstro",
    description=(
        "Busca um monstro no Divine Pride. Informe `monster_id` para a consulta "
        "exata ou `nome` para busca por texto. Traz raça, elemento, tamanho, DEF, "
        "MDEF, HIT e FLEE — os dados que orientam escolha de carta e de elemento."
    ),
)
def buscar_monstro(monster_id: int | None = None, nome: str | None = None) -> dict[str, Any]:
    if monster_id is None and not nome:
        return {"ok": False, "erro": "Informe `monster_id` ou `nome`."}
    try:
        with DivinePrideClient() as client:
            if monster_id is not None:
                alvo = client.monster_target(monster_id)
                return {"ok": True, "monstro": alvo.model_dump()}
            resultados = client.search(nome or "", kind="monster")
    except RagdataError as exc:
        return _erro(exc)
    return {
        "ok": True,
        "resultados": resultados,
        "nota": "Busca por nome é melhor-esforço; use `monster_id` para dados completos.",
    }


@server.tool(
    name="buscar_equipamento_por_necessidade",
    description=(
        "Acha armas e armaduras do Ragnarok LATAM que atendem a uma necessidade, usando o Divine Pride "
        "(só itens listados na base LATAM). Informe `tipo` + `alvo` (preferível) ou `necessidade` em texto "
        "livre. Tipos: " + ", ".join(k.value for k in needs.NeedKind) + ". Alvos: raça (amorfo, morto-vivo, "
        "bruto, planta, inseto, peixe, demonio, humanoide, anjo, dragao, jogador), propriedade (neutro, agua, "
        "terra, fogo, vento, veneno, sagrado, sombrio, fantasma, maldito), tamanho (pequeno, medio, grande), "
        "atributo (FOR, AGI, VIT, INT, DES, SOR) ou o nome da habilidade em português do LATAM. `categorias`: "
        "arma, armadura, carta, sombra (padrão: armadura e arma). `subtipos`: capa, bota, escudo, acessório, "
        "espada de duas mãos… `classes`: nomes de classe. Cada item vem com as linhas da descrição que "
        "atendem ao pedido, a condição (refino/conjunto) e o percentual, ordenado do maior para o menor. "
        "Lê até `limite_detalhes` itens na API a 1 req/s — use filtros para buscas amplas."
    ),
)
def buscar_equipamento_por_necessidade(
    necessidade: str | None = None,
    tipo: str | None = None,
    alvo: str | None = None,
    categorias: list[str] | None = None,
    subtipos: list[str] | None = None,
    classes: list[str] | None = None,
    nivel_min: int | None = None,
    nivel_max: int | None = None,
    slots_min: int | None = None,
    limite_detalhes: int = 25,
    paginas: int = 3,
    detalhes: bool = True,
) -> dict[str, Any]:
    if not necessidade and not (tipo and alvo):
        return {
            "ok": False,
            "erro": "Informe `necessidade` (texto livre) ou `tipo` + `alvo`.",
            "vocabulario": needs.vocabulary(),
        }
    try:
        need = needs.parse_need(necessidade or alvo or "", kind=tipo, target=alvo)
        options = needs.SearchOptions(
            categories=needs.resolve_categories(categorias),
            sub_types=needs.resolve_sub_types(subtipos),
            job_ids=needs.resolve_job_ids(classes),
            min_level=nivel_min,
            max_level=nivel_max,
            min_slots=slots_min,
            max_pages=max(1, paginas),
            max_details=max(0, limite_detalhes),
            details=detalhes,
        )
    except ValueError as exc:
        return {"ok": False, "erro": str(exc), "vocabulario": needs.vocabulary()}
    try:
        with DivinePrideClient() as client:
            resultado = needs.find_equipment(client, need, options)
    except RagdataError as exc:
        return _erro(exc)
    return {"ok": True, **resultado}


@server.tool(
    name="vocabulario_de_necessidades",
    description=(
        "Tipos de necessidade, alvos aceitos (raça, propriedade, tamanho, atributo) e exemplos para "
        "`buscar_equipamento_por_necessidade`."
    ),
)
def vocabulario_de_necessidades() -> dict[str, Any]:
    return {"ok": True, **needs.vocabulary()}


@server.tool(
    name="consultar_browiki",
    description=(
        "Lê uma página do browiki (wiki brasileira de Ragnarok) — útil para "
        "descrição de classe, requisitos de habilidade e mecânicas. Aceita o "
        "título exato ou um termo de busca."
    ),
)
def consultar_browiki(termo: str, max_chars: int = 6000) -> dict[str, Any]:
    try:
        with BrowikiClient() as client:
            page = client.lookup(termo, max_chars=max_chars)
    except RagdataError as exc:
        return _erro(exc)
    return {"ok": True, **page}


@server.tool(
    name="estado_do_ragdata",
    description="Diagnóstico: tabelas de jogo baixadas, cache e chave da API configurada.",
)
def estado_do_ragdata() -> dict[str, Any]:
    settings = get_settings()
    faltando = missing_tables(settings)
    return {
        "ok": True,
        "cache": str(settings.cache_dir),
        "servidor_divine_pride": settings.divine_pride_server,
        "api_key_configurada": bool(settings.divine_pride_api_key),
        "tabelas_faltando": faltando,
        "pronto": not faltando,
        "proxima_acao": (
            "Rode `ragdata setup` no terminal." if faltando
            else "Defina DIVINE_PRIDE_API_KEY para consultar itens e monstros."
            if not settings.divine_pride_api_key
            else None
        ),
    }


def main() -> None:  # pragma: no cover - ponto de entrada
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
