"""Linha de comando do ragdata."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import analysis, ingest
from .config import RATHENA_TABLES, get_settings
from .engine import compute
from .errors import RagdataError
from .models import Goal
from .setup_data import download_tables, missing_tables
from .sources import BrowikiClient, DivinePrideClient

app = typer.Typer(
    add_completion=False,
    help="Auxiliar de build para Ragnarok Online Renewal (LATAM).",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

_SEV_STYLE = {"critico": "bold red", "aviso": "yellow", "dica": "cyan"}
_SEV_LABEL = {"critico": "CRÍTICO", "aviso": "AVISO", "dica": "DICA"}


def _fail(message: str) -> None:
    err_console.print(f"[bold red]erro:[/] {message}")
    raise typer.Exit(code=1)


def _load(path: str):
    try:
        return ingest.load_character_file(path)
    except (RagdataError, OSError) as exc:
        _fail(str(exc))


def _print_json(data: dict, *, raw: bool) -> None:
    """Imprime a resposta; sem `--raw`, esconde o payload bruto da fonte."""
    if not raw:
        data = {k: v for k, v in data.items() if k != "raw"}
    console.print_json(json.dumps(data, ensure_ascii=False))


def _stats_table(derived) -> Table:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim")
    table.add_column(style="bold")
    table.add_row("ATK", derived.atk_display)
    table.add_row("MATK", derived.matk_display)
    table.add_row("HIT / FLEE", f"{derived.hit} / {derived.flee}")
    table.add_row("CRIT / Esq. perfeita", f"{derived.crit} / {derived.perfect_dodge}")
    table.add_row("DEF", derived.def_display)
    table.add_row("MDEF", derived.mdef_display)
    table.add_row(
        "ASPD",
        f"{derived.aspd} (teto {derived.aspd_cap}) — {derived.attacks_per_second} ataques/s",
    )
    table.add_row("HP / SP", f"{derived.max_hp} / {derived.max_sp}")
    if any((derived.patk, derived.smatk, derived.res, derived.mres)):
        table.add_row("PAtk / SMatk", f"{derived.patk} / {derived.smatk}")
        table.add_row("RES / MRES", f"{derived.res} / {derived.mres}")
    cast = (
        "instantânea"
        if derived.instant_cast
        else f"-{derived.variable_cast_total_reduction:.1%} de conjuração variável"
    )
    table.add_row("Conjuração", cast)
    return table


@app.command()
def setup(
    force: Annotated[bool, typer.Option("--force", help="Rebaixa mesmo se já existir.")] = False,
) -> None:
    """Baixa as tabelas de jogo do rAthena para o cache local."""
    settings = get_settings()
    console.print(f"Baixando {len(RATHENA_TABLES)} tabelas para [bold]{settings.gamedata_dir}[/]…")
    try:
        results = download_tables(settings, force=force)
    except RagdataError as exc:
        _fail(str(exc))
    for result in results:
        estado = "[dim]já existia[/]" if result.skipped else f"{result.bytes_written // 1024} KiB"
        console.print(f"  [green]✓[/] {result.name}  {estado}")
    console.print("\n[green]Pronto.[/] Agora configure DIVINE_PRIDE_API_KEY para usar o Divine Pride.")


@app.command()
def stats(arquivo: Annotated[str, typer.Argument(help="YAML ou JSON do personagem")]) -> None:
    """Mostra só os stats derivados."""
    character = _load(arquivo)
    try:
        derived = compute(character)
    except RagdataError as exc:
        _fail(str(exc))
    titulo = f"{character.name or 'Personagem'} — {derived.job_key} {derived.base_level}/{derived.job_level}"
    console.print(Panel(_stats_table(derived), title=titulo, border_style="blue"))
    for aviso in derived.warnings:
        console.print(f"[yellow]![/] {aviso}")


@app.command()
def analyze(
    arquivo: Annotated[str, typer.Argument(help="YAML ou JSON do personagem")],
    goal: Annotated[Goal | None, typer.Option(help="Objetivo da build.")] = None,
    monster_id: Annotated[
        int | None, typer.Option("--monster-id", help="ID de um monstro-alvo no Divine Pride.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Analisa a build: números, desperdícios e sugestões."""
    character = _load(arquivo)
    target = None
    if monster_id is not None:
        try:
            with DivinePrideClient() as client:
                target = client.monster_target(monster_id)
        except RagdataError as exc:
            _fail(str(exc))
    try:
        report = analysis.analyze(character, goal=goal, target=target)
    except RagdataError as exc:
        _fail(str(exc))

    if as_json:
        console.print_json(json.dumps(report.to_dict(), ensure_ascii=False))
        return

    derived = report.derived
    titulo = f"{character.name or 'Personagem'} — {derived.job_key} {derived.base_level}/{derived.job_level}"
    console.print(Panel(_stats_table(derived), title=titulo, border_style="blue"))

    audit = report.audit
    cor = "green" if audit.consistent else "red"
    console.print(
        f"\n[bold]Pontos de atributo:[/] {audit.spent} gastos de {audit.available} "
        f"([{cor}]{audit.remaining} sobrando[/])"
    )
    if target is not None:
        console.print(f"[bold]Alvo:[/] {target.name}")

    console.print("\n[bold]Achados[/]")
    if not report.findings:
        console.print("  [green]Nada a apontar.[/]")
    for finding in report.findings:
        estilo = _SEV_STYLE[finding.severity]
        console.print(f"  [{estilo}]{_SEV_LABEL[finding.severity]}[/] {finding.title}")
        console.print(f"        [dim]{finding.detail}[/]")

    for aviso in derived.warnings:
        console.print(f"\n[yellow]![/] {aviso}")


@app.command()
def simulate(
    arquivo: Annotated[str, typer.Argument(help="YAML ou JSON do personagem")],
    changes: Annotated[str, typer.Argument(help='JSON com as mudanças, ex: \'{"stats":{"agi":120}}\'')],
) -> None:
    """Compara a build atual com uma versão alterada."""
    character = _load(arquivo)
    try:
        payload = json.loads(changes)
    except json.JSONDecodeError as exc:
        _fail(f"As mudanças precisam ser um JSON válido: {exc}")
    try:
        resultado = analysis.simulate(character, payload)
    except (RagdataError, ValueError) as exc:
        _fail(str(exc))

    if not resultado["diferencas"]:
        console.print("[yellow]Nenhum número mudou com essa alteração.[/]")
    table = Table(title="Diferenças")
    table.add_column("Campo")
    table.add_column("Antes", justify="right")
    table.add_column("Depois", justify="right")
    table.add_column("Δ", justify="right")
    for campo, valores in resultado["diferencas"].items():
        delta = valores["delta"]
        cor = "green" if isinstance(delta, (int, float)) and delta > 0 else "red"
        table.add_row(
            campo,
            str(valores["antes"]),
            str(valores["depois"]),
            f"[{cor}]{delta:+}[/]" if delta is not None else "—",
        )
    console.print(table)
    pontos = resultado["pontos"]
    console.print(
        f"Pontos gastos: {pontos['antes']['gastos']} → {pontos['depois']['gastos']} "
        f"(sobrando {pontos['depois']['sobrando']})"
    )
    if resultado["aviso"]:
        console.print(f"[bold red]{resultado['aviso']}[/]")


@app.command()
def item(
    item_id: Annotated[int, typer.Argument(help="ID do item no Divine Pride")],
    raw: Annotated[bool, typer.Option("--raw", help="Mostra o JSON bruto.")] = False,
) -> None:
    """Consulta um item no Divine Pride."""
    try:
        with DivinePrideClient() as client:
            data = client.item(item_id)
    except RagdataError as exc:
        _fail(str(exc))
    _print_json(data, raw=raw)


@app.command()
def monster(
    monster_id: Annotated[int, typer.Argument(help="ID do monstro no Divine Pride")],
    raw: Annotated[bool, typer.Option("--raw", help="Mostra o JSON bruto.")] = False,
) -> None:
    """Consulta um monstro no Divine Pride."""
    try:
        with DivinePrideClient() as client:
            data = client.monster(monster_id)
    except RagdataError as exc:
        _fail(str(exc))
    _print_json(data, raw=raw)


@app.command()
def wiki(
    termo: Annotated[str, typer.Argument(help="Título ou busca no browiki")],
    max_chars: Annotated[int, typer.Option(help="Tamanho máximo do texto.")] = 4000,
) -> None:
    """Lê uma página do browiki."""
    try:
        with BrowikiClient() as client:
            page = client.lookup(termo, max_chars=max_chars)
    except RagdataError as exc:
        _fail(str(exc))
    console.print(Panel(page["text"], title=page["title"], subtitle=page["url"], border_style="green"))


@app.command()
def doctor() -> None:
    """Verifica a instalação: tabelas, cache, chave de API e campos do Divine Pride."""
    settings = get_settings()
    console.print(f"[bold]Cache:[/] {settings.cache_dir}")
    console.print(f"[bold]Servidor Divine Pride:[/] {settings.divine_pride_server}")

    faltando = missing_tables(settings)
    if faltando:
        console.print(f"  [red]✗[/] tabelas faltando: {', '.join(faltando)} — rode `ragdata setup`")
    else:
        console.print(f"  [green]✓[/] {len(RATHENA_TABLES)} tabelas de jogo presentes")

    if not settings.divine_pride_api_key:
        console.print("  [yellow]![/] DIVINE_PRIDE_API_KEY não definida — consultas ao Divine Pride vão falhar")
        raise typer.Exit(code=0)

    console.print("  [green]✓[/] DIVINE_PRIDE_API_KEY definida")
    console.print("\n[bold]Testando a normalização de campos do Divine Pride…[/]")
    # 1201 = Knife, 1002 = Poring: existem em qualquer base.
    for kind, entity_id in (("item", 1201), ("monstro", 1002)):
        try:
            with DivinePrideClient(settings) as client:
                data = client.item(entity_id) if kind == "item" else client.monster(entity_id)
        except RagdataError as exc:
            console.print(f"  [red]✗[/] {kind} {entity_id}: {exc}")
            continue
        vazios = [k for k, v in data.items() if k != "raw" and v in (None, "")]
        if vazios:
            console.print(
                f"  [yellow]![/] {kind} {entity_id}: campos sem valor → {', '.join(vazios)}"
            )
            console.print(
                f"        [dim]chaves recebidas: {', '.join(sorted(data['raw']))}[/]"
            )
        else:
            console.print(f"  [green]✓[/] {kind} {entity_id}: todos os campos mapeados")


def main() -> None:  # pragma: no cover - ponto de entrada
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
