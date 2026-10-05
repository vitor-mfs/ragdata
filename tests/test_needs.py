"""Busca por necessidade: interpretação, leitura da descrição e orquestração (tudo offline).

Os textos de item são cópias das descrições reais da base LATAM do Divine Pride,
para que o reconhecimento de linhas seja testado contra o que o jogo mostra.
"""

from __future__ import annotations

import html

import httpx
import pytest
from typer.testing import CliRunner

from ragdata import cli, mcp_server, needs
from ragdata.cache import Cache
from ragdata.errors import ConfigError, SourceError
from ragdata.needs import NeedKind, SearchOptions, find_equipment, match_lines, parse_need
from ragdata.sources.divinepride import DivinePrideClient, parse_item_listing

# --- descrições reais (LATAM, pt) --------------------------------------------

SOPRO_DO_DRAGAO = (
    "Uma capa que causa uma grande sensação de calor, como um sopro de dragão.\n"
    "-------------------------\n"
    "Resistência a raça Dragão +15%\n"
    "-------------------------\n"
    "Ao equipar a Matadora de Dragão, Caçadora de Dragões ou a Gae Bolg:\n"
    "Dano contra a raça Dragão +5%.\n"
    "-------------------------\n"
    "Tipo: Capa\nDEF: 16 DEFM: 0\nPeso: 60\nNível necessário: 48\n"
    "Classes: Todas as classes transcendentais, exceto aprendizes"
)

BOTA_DRACONICA = (
    "Um par de botas feitas através da tecelagem de várias partes de dragões poderosos.\n"
    "--------------------------\n"
    "SP máx. +500.\nConjuração variável -7%.\nVelocidade de ataque +7%.\n"
    "Refino +7 ou mais:\nResistência a raça Dragão +2%.\n"
    "Refino +9 ou mais:\nAo receber danos físicos ou mágicos, 4% de chance de ativar um [Efeito] por 4 segndos.\n"
    "Refino +11 ou mais:\nResistência a raça Dragão +3% adicional.\n"
    "--------------------------\n"
    "Efeito:\nA cada segundo:\nRecupera 500 de SP.\n"
    "--------------------------\n"
    "Tipo: Calçado\nDEF: 35 DEFM: 0\nPeso: 60\nNível necessário: 170\nClasses: Todas"
)

ELMO_DO_DRAGAO = (
    "Um elmo criado pela Sociedade Draconiana.\n"
    "--------------------------\n"
    "FOR, INT e DES +2.\n"
    "Refino +7 ou mais:\nEXP adquirida pela raça Dragão +3%.\n"
    "Refino +9 ou mais:\nEXP adquirida pela raça Dragão +5% adicional.\n"
    "--------------------------\n"
    "Conjunto\n[Colete do Dragão]\n[Manto do Dragão]\nResistência a raça Dragão +20%.\n"
    "--------------------------\n"
    "Conjunto\n[Sopro do Dragão] e\n[Caçadora de Dragões] ou\n[Matadora de Dragão] ou\n[Gae Bolg]\n"
    "Dano físico contra a raça Dragão +25%.\n"
    "--------------------------\n"
    "Tipo: Equip. para Cabeça\nEquipa em: Topo\nDEF: 10 DEFM: 0\nPeso: 200\nNível necessário: 50\nClasses: Todas"
)

COTA_DRACONICA_AZUL = (
    "Armadura que contém um imenso poder draconiano acumulado ao longo dos milênios.\n"
    "--------------------------\n"
    "INT +12.\nHP máx. +5%.\nSP máx. +20%.\n"
    "--------------------------\n"
    "A cada 2 refinos:\nDano mágico +2%.\nEfetividade de cura +4%.\n"
    "A cada 3 refinos:\nDano mágico contra a raça Dragão +7%.\n"
    "Refino +11 ou mais:\nConjuração fixa -0,2 segundos.\n"
    "Dano mágico contra os tamanhos Médio e Grande +10%."
)

BASTARDA_ANCESTRAL = (
    "Uma espada digna apenas dos que não mentem a idade.\n"
    "-------------------------\n"
    "A cada 2 refinos:\nHP e SP máx. +3%.\n"
    "A cada 3 refinos:\nDano de [Sopro do Dragão] e [Bafo do Dragão] +5%.\n"
    "-------------------------\n"
    "Refino +9 ou mais:\nPós-conjuração -10%."
)

BACULO_ANCESTRAL = (
    "Transparente e poderoso.<br />-------------------------<br />"
    "A cada 2 refinos:<br />ATQM +10.<br />A cada 3 refinos:<br />Dano de [Esquife de Gelo] +12%.<br />"
    "-------------------------<br />Refino +9 ou mais:<br />Dano mágico de propriedade Água +7%.<br />"
    "Refino +11 ou mais:<br />Recarga de [Esquife de Gelo] -1 segundo.<br />"
    "Dano mágico contra oponentes de propriedade Fogo +7%."
)

ANEL_SENHOR_DAS_CHAMAS = (
    "Um anel imbuído do poder do espírito-rei do fogo.\n"
    "-------------------------\n"
    "ATQ +15.\nFOR +2. VIT +1.\nResistência a propriedade Fogo +10%.\n"
    "-------------------------\n"
    "Ao realizar ataques físicos:\n"
    "Chances de autoconjurar [Zen] nv. 1, [Fúria Interior] nv. 1, [Gloria Domini] nv. 2, "
    "[Bolas de Fogo] nv. 1 e [Impacto de Tyr] nv. 5."
)


# --- interpretação ------------------------------------------------------------


class TestParseNeed:
    @pytest.mark.parametrize(
        ("texto", "tipo", "alvo", "palavra"),
        [
            ("resistência a dragão", NeedKind.RESIST_RACE, "dragao", "Dragão"),
            ("resistencia a dragao", NeedKind.RESIST_RACE, "dragao", "Dragão"),
            ("dragon resistance", NeedKind.RESIST_RACE, "dragao", "Dragão"),
            ("dano em amorfo", NeedKind.DAMAGE_RACE, "amorfo", "Amorfo"),
            ("damage to formless", NeedKind.DAMAGE_RACE, "amorfo", "Amorfo"),
            ("dano contra morto-vivo", NeedKind.DAMAGE_RACE, "morto-vivo", "Morto-Vivo"),
            ("dano em demi-humano", NeedKind.DAMAGE_RACE, "humanoide", "Humanoide"),
            ("dano mágico em dragão", NeedKind.MAGIC_DAMAGE_RACE, "dragao", "Dragão"),
            ("resistência a fogo", NeedKind.RESIST_ELEMENT, "fogo", "Fogo"),
            ("resistência a propriedade sombria", NeedKind.RESIST_ELEMENT, "sombrio", "Sombrio"),
            ("resistência a morto-vivo", NeedKind.RESIST_RACE, "morto-vivo", "Morto-Vivo"),
            ("resistência a propriedade maldita", NeedKind.RESIST_ELEMENT, "maldito", "Maldito"),
            ("dano de fogo", NeedKind.DAMAGE_ELEMENT, "fogo", "Fogo"),
            ("dano mágico contra fogo", NeedKind.MAGIC_DAMAGE_ELEMENT, "fogo", "Fogo"),
            ("dano em tamanho grande", NeedKind.DAMAGE_SIZE, "grande", "Grande"),
            ("dano mágico em grande", NeedKind.MAGIC_DAMAGE_SIZE, "grande", "Grande"),
            ("dano de [Sopro do Dragão]", NeedKind.SKILL_DAMAGE, "Sopro do Dragão", "Sopro do Dragão"),
            ("dano de Sopro do Dragão", NeedKind.SKILL_DAMAGE, "Sopro do Dragão", "Sopro do Dragão"),
            ("habilidade: Sopro do Dragão", NeedKind.SKILL_DAMAGE, "Sopro do Dragão", "Sopro do Dragão"),
            ("recarga de Esquife de Gelo", NeedKind.SKILL_COOLDOWN, "Esquife de Gelo", "Esquife de Gelo"),
            ("FOR", NeedKind.STAT, "for", "FOR"),
            ("+INT", NeedKind.STAT, "int", "INT"),
        ],
    )
    def test_texto_livre(self, texto: str, tipo: NeedKind, alvo: str, palavra: str) -> None:
        need = parse_need(texto)
        assert need.kind is tipo
        assert need.target == alvo
        assert need.keyword == palavra

    def test_estruturado(self) -> None:
        need = parse_need("ignorado", kind="resistencia_raca", target="Dragon")
        assert need.kind is NeedKind.RESIST_RACE
        assert need.keyword == "Dragão"
        assert need.spec.function_id == 25

    def test_estruturado_habilidade(self) -> None:
        need = parse_need("[Sopro do Dragão]", kind=NeedKind.SKILL_DAMAGE)
        assert need.keyword == "Sopro do Dragão"
        assert need.label == "Dano de [Sopro do Dragão]"

    def test_tipo_desconhecido(self) -> None:
        with pytest.raises(ValueError, match="Tipo de necessidade desconhecido"):
            parse_need("x", kind="virar_peixe")

    def test_alvo_desconhecido(self) -> None:
        with pytest.raises(ValueError, match="Opções"):
            parse_need("x", kind="resistencia_raca", target="poring")

    def test_texto_sem_sentido(self) -> None:
        with pytest.raises(ValueError, match="Não entendi"):
            parse_need("quero ficar bonito")

    def test_resistencia_a_tamanho_nao_existe(self) -> None:
        with pytest.raises(ValueError, match="tamanho"):
            parse_need("resistência a tamanho grande")

    def test_vocabulario_lista_todos_os_tipos(self) -> None:
        vocab = needs.vocabulary()
        assert set(vocab["tipos"]) == {k.value for k in NeedKind}
        assert vocab["alvos"]["raca"]["dragao"] == "Dragão"


# --- leitura da descrição -------------------------------------------------------


class TestMatchLines:
    def test_linha_simples(self) -> None:
        [m] = match_lines(SOPRO_DO_DRAGAO, parse_need("resistência a dragão"))
        assert m.text.startswith("Resistência a raça Dragão +15%")
        assert m.value == 15 and m.percent is True
        assert m.context is None and m.in_set is False

    def test_condicao_de_refino_vira_contexto(self) -> None:
        matches = match_lines(BOTA_DRACONICA, parse_need("resistência a dragão"))
        assert [(m.value, m.context) for m in matches] == [
            (2.0, "Refino +7 ou mais:"),
            (3.0, "Refino +11 ou mais:"),
        ]

    def test_conjunto_vira_contexto(self) -> None:
        [m] = match_lines(ELMO_DO_DRAGAO, parse_need("resistência a dragão"))
        assert m.in_set is True
        assert m.context == "Conjunto com [Colete do Dragão], [Manto do Dragão]"
        assert m.value == 20

    def test_conjunto_com_alternativas(self) -> None:
        [m] = match_lines(ELMO_DO_DRAGAO, parse_need("dano em dragão"))
        assert m.value == 25
        assert m.context == "Conjunto com [Sopro do Dragão], [Caçadora de Dragões], [Matadora de Dragão], [Gae Bolg]"

    def test_exp_nao_e_dano_nem_resistencia(self) -> None:
        textos = [m.text for m in match_lines(ELMO_DO_DRAGAO, parse_need("dano em dragão"))]
        assert all("EXP" not in t for t in textos)

    def test_dano_generico_inclui_fisico_e_magico(self) -> None:
        assert [m.value for m in match_lines(COTA_DRACONICA_AZUL, parse_need("dano em dragão"))] == [7.0]
        assert [m.value for m in match_lines(COTA_DRACONICA_AZUL, parse_need("dano mágico em dragão"))] == [7.0]
        assert match_lines(ELMO_DO_DRAGAO, parse_need("dano mágico em dragão")) == []

    def test_tamanho(self) -> None:
        [m] = match_lines(COTA_DRACONICA_AZUL, parse_need("dano mágico em tamanho grande"))
        assert m.text == "Dano mágico contra os tamanhos Médio e Grande +10%."
        assert m.context == "Refino +11 ou mais:"

    def test_habilidade_entre_colchetes(self) -> None:
        [m] = match_lines(BASTARDA_ANCESTRAL, parse_need("dano de Sopro do Dragão"))
        assert m.value == 5 and m.context == "A cada 3 refinos:"
        assert match_lines(BASTARDA_ANCESTRAL, parse_need("dano de Bafo do Dragão"))
        assert match_lines(BASTARDA_ANCESTRAL, parse_need("dano de Esquife de Gelo")) == []

    def test_descricao_com_br_e_recarga_negativa(self) -> None:
        [m] = match_lines(BACULO_ANCESTRAL, parse_need("recarga de Esquife de Gelo"))
        assert m.value == -1 and m.percent is False
        [dano] = match_lines(BACULO_ANCESTRAL, parse_need("dano de Esquife de Gelo"))
        assert dano.value == 12

    def test_propriedade(self) -> None:
        [m] = match_lines(ANEL_SENHOR_DAS_CHAMAS, parse_need("resistência a fogo"))
        assert m.value == 10
        # "[Bolas de Fogo]" é autoconjuração, não dano contra a propriedade.
        assert match_lines(ANEL_SENHOR_DAS_CHAMAS, parse_need("dano em fogo")) == []
        [magico] = match_lines(BACULO_ANCESTRAL, parse_need("dano mágico contra fogo"))
        assert magico.text == "Dano mágico contra oponentes de propriedade Fogo +7%."

    def test_atributo(self) -> None:
        [m] = match_lines(ELMO_DO_DRAGAO, parse_need("FOR"))
        assert m.text == "FOR, INT e DES +2."
        assert match_lines(COTA_DRACONICA_AZUL, parse_need("FOR")) == []

    def test_sem_linha_reconhecivel(self) -> None:
        assert match_lines(COTA_DRACONICA_AZUL, parse_need("resistência a dragão")) == []


# --- listagem falsa do site -----------------------------------------------------

TODOS = ("bRO", "dpRO", "iRO", "kROM", "LATAM")
SEM_LATAM = ("bRO", "dpRO", "kROM")


def _listing_html(
    rows: list[tuple[int, str, str, str, str, tuple[str, ...]]],
    *,
    total: int,
    region: str = "LATAM",
    language: str = "Portuguese",
    page_links: tuple[int, ...] = (),
) -> str:
    """Reproduz o HTML da listagem de itens do Divine Pride (versão 2026)."""
    trs = []
    for item_id, name, type_, sub_type, level, servers in rows:
        badges = "".join(
            f'<span class="badge badge-{"primary" if s == "LATAM" else "secondary"}" '
            f'style="font-size: var(--font-size-xs);">{s}</span>'
            for s in servers
        )
        trs.append(
            f"""<tr onclick="window.location='/database/item/{item_id}'" style="cursor: pointer;">
                <td class="font-semibold"><div>
                    <img src="https://static.divine-pride.net/images/items/item/{item_id}.png" alt="" />
                    <a href="/database/item/{item_id}" onclick="event.stopPropagation()">{html.escape(name)}</a>
                </div></td>
                <td><span class="badge badge-primary">{type_}</span></td>
                <td>{sub_type}</td>
                <td>{level}</td>
                <td>0</td>
                <td><div>{badges}</div></td>
            </tr>"""
        )
    links = "".join(f'<a href="/database/item/armor?function=25&amp;page={p}">{p}</a>' for p in page_links)
    return f"""<!DOCTYPE html><html><body>
    <button type="button" class="header-select nav-dropdown-trigger language-trigger">
        <span class="flag-icon flag-sprite"></span>
        <span>{region} &middot; {language}</span>
    </button>
    <section><div><span class="text-muted">{total} results</span></div>
    <table class="sortable server-sort">
        <thead><tr><th data-sort="name">Name</th><th>Type</th><th>SubType</th>
        <th>Required Level</th><th>Sell Price</th><th>Server</th></tr></thead>
        <tbody>{"".join(trs)}</tbody>
    </table>
    <nav>{links}</nav></section></body></html>"""


PAGINA_1 = [
    (2527, "Sopro do Dragão [1]", "Armor", "Garment", "48", TODOS),
    (22208, "Bota Dracônica [1]", "Armor", "Shoes", "170", TODOS),
    (5467, "Elmo do Dragão [1]", "Armor", "Headgear", "50", TODOS),
    (15395, "Cota Dracônica Azul [1]", "Armor", "Armor", "170", TODOS),
    (20949, "[1]", "Armor", "Garment", "100", TODOS),  # sem nome em português
    (5635, "Crânio de Dragão", "Armor", "Headgear", "", SEM_LATAM),  # não existe no LATAM
]
PAGINA_2 = [
    (2399, "Colete do Dragão [1]", "Armor", "Armor", "1", TODOS),  # a API dirá 404 no LATAM
    (2115, "Escudo da Valquíria [1]", "Armor", "Shield", "65", TODOS),  # a API devolverá outra região
]

DESCRICOES = {
    2527: SOPRO_DO_DRAGAO,
    22208: BOTA_DRACONICA,
    5467: ELMO_DO_DRAGAO,
    15395: COTA_DRACONICA_AZUL,
}
NOMES = {
    2527: "Sopro do Dragão [1]",
    22208: "Bota Dracônica [1]",
    5467: "Elmo do Dragão [1]",
    15395: "Cota Dracônica Azul [1]",
    2115: "Escudo da Valquíria [1]",
}


def _fake_site_and_api(log: list[httpx.Request]):
    """Transporte falso: listagem de armaduras em 2 páginas, armas vazias, API por ID."""

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        path = request.url.path
        if path == "/database/item/armor":
            assert request.headers["accept-language"].startswith("pt-BR")
            assert request.url.params["function"] == "25"
            assert request.url.params["description"] == "Dragão"
            page = int(request.url.params.get("page", "1"))
            rows = PAGINA_1 if page == 1 else PAGINA_2
            return httpx.Response(200, text=_listing_html(rows, total=8, page_links=(2,)))
        if path == "/database/item/weapon":
            return httpx.Response(200, text=_listing_html([], total=0))
        if path.startswith("/api/database/Item/"):
            assert request.headers["x-server"] == "LATAM"
            assert request.headers["accept-language"] == "pt"
            item_id = int(path.rsplit("/", 1)[1])
            if item_id == 2399:
                return httpx.Response(404)
            region = "bRO" if item_id == 2115 else "LATAM"
            return httpx.Response(
                200,
                json={
                    "id": item_id,
                    "name": NOMES[item_id],
                    "description": DESCRICOES.get(item_id, "Sem efeitos."),
                    "slots": 1,
                    "type": "Armor",
                    "region": region,
                },
            )
        raise AssertionError(f"URL inesperada: {request.url}")

    return handler


def _client(settings, handler, sem_espera) -> DivinePrideClient:
    return DivinePrideClient(
        settings,
        client=httpx.Client(base_url="https://www.divine-pride.net", transport=httpx.MockTransport(handler)),
        cache=Cache(settings.http_cache_path, settings.cache_ttl_seconds),
        limiter=sem_espera,
    )


class TestListagem:
    def test_parse_le_regiao_total_e_badges(self) -> None:
        listing = parse_item_listing(_listing_html(PAGINA_1, total=8, page_links=(2,)))
        assert (listing.region, listing.language) == ("LATAM", "Portuguese")
        # 8 resultados cabem em 1 página, mas o link de paginação diz que há uma 2ª: vale o maior.
        assert listing.total == 8 and listing.pages == 2
        assert len(listing.rows) == 6
        sopro = listing.rows[0]
        assert sopro.id == 2527 and sopro.sub_type == "Garment" and sopro.required_level == 48
        assert sopro.slots == 1 and sopro.on_latam and sopro.has_name
        assert listing.rows[4].has_name is False
        assert listing.rows[5].on_latam is False and listing.rows[5].required_level is None

    def test_parse_html_inesperado_vem_vazio(self) -> None:
        listing = parse_item_listing("<html><body>nada</body></html>")
        assert listing.rows == [] and listing.total is None and listing.pages == 0

    def test_list_items_monta_os_filtros_e_usa_cache(self, settings, sem_espera) -> None:
        log: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            log.append(request)
            return httpx.Response(200, text=_listing_html(PAGINA_1, total=6))

        with _client(settings, handler, sem_espera) as client:
            listing = client.list_items(
                "armor",
                function_id=25,
                description="Dragão",
                sub_types=["Garment", "Shoes"],
                job_ids=[4054],
                min_level=100,
                max_level=200,
                page=2,
            )
            client.list_items(
                "armor", function_id=25, description="Dragão", sub_types=["Garment", "Shoes"],
                job_ids=[4054], min_level=100, max_level=200, page=2,
            )

        assert len(log) == 1, "a segunda chamada deveria vir do cache"
        params = log[0].url.params
        assert params["function"] == "25" and params["description"] == "Dragão"
        assert params.get_list("subTypes") == ["Garment", "Shoes"]
        assert params["jobGroups"] == "4054" and params["minRequiredLevel"] == "100"
        assert params["maxRequiredLevel"] == "200" and params["page"] == "2"
        assert log[0].headers["accept-language"] == "pt-BR,pt;q=0.9"
        assert listing.page == 2 and len(listing.rows) == 6

    def test_categoria_invalida(self, settings, sem_espera) -> None:
        with _client(settings, lambda r: httpx.Response(500), sem_espera) as client:
            with pytest.raises(ValueError, match="Categoria desconhecida"):
                client.list_items("poring")

    def test_erro_do_site(self, settings, sem_espera) -> None:
        with _client(settings, lambda r: httpx.Response(503), sem_espera) as client:
            with pytest.raises(SourceError, match="503"):
                client.list_items("armor", function_id=25)


class TestFindEquipment:
    def test_fluxo_completo(self, settings, sem_espera) -> None:
        log: list[httpx.Request] = []
        need = parse_need("resistência a dragão")
        with _client(settings, _fake_site_and_api(log), sem_espera) as client:
            resultado = find_equipment(client, need)

        assert resultado["servidor"] == "LATAM"
        assert resultado["categorias"] == ["armor", "weapon"]
        assert resultado["necessidade"]["funcao_divine_pride"] == 25
        # 6 + 2 linhas, menos a sem nome e a sem badge LATAM.
        assert resultado["candidatos"] == 6
        assert resultado["excluidos"]["sem_nome_latam"] == 1
        # 1 sem badge na listagem + 404 na API + região errada na API.
        assert resultado["excluidos"]["fora_do_latam"] == 3

        nomes = [item["nome"] for item in resultado["itens"]]
        assert nomes == ["Elmo do Dragão [1]", "Sopro do Dragão [1]", "Bota Dracônica [1]"]
        assert [item["melhor_valor"] for item in resultado["itens"]] == [20.0, 15.0, 3.0]
        elmo = resultado["itens"][0]
        assert elmo["efeitos"][0]["conjunto"] is True
        assert elmo["url"].endswith("/database/item/5467")
        assert elmo["categoria"] == "armor" and elmo["subtipo"] == "Headgear"

        assert [item["nome"] for item in resultado["possiveis"]] == ["Cota Dracônica Azul [1]"]
        assert resultado["possiveis"][0]["descricao"] == COTA_DRACONICA_AZUL
        assert resultado["nao_consultados"] == []

        # Listagem: 2 páginas de armadura + 1 de arma; API: 6 candidatos.
        chamadas_site = [r for r in log if r.url.path.startswith("/database/item/")]
        chamadas_api = [r for r in log if r.url.path.startswith("/api/")]
        assert len(chamadas_site) == 3 and len(chamadas_api) == 6

    def test_limite_de_detalhes_e_sem_detalhes(self, settings, sem_espera) -> None:
        log: list[httpx.Request] = []
        need = parse_need("resistência a dragão")
        with _client(settings, _fake_site_and_api(log), sem_espera) as client:
            parcial = find_equipment(client, need, SearchOptions(categories=("armor",), max_details=2))
            nada = find_equipment(client, need, SearchOptions(categories=("armor",), details=False))

        assert parcial["consultados"] == 2 and len(parcial["nao_consultados"]) == 4
        assert any("não foram consultados" in nota for nota in parcial["notas"])
        assert nada["consultados"] == 0 and nada["itens"] == []
        assert [i["id"] for i in nada["nao_consultados"]] == [2527, 22208, 5467, 15395, 2399, 2115]

    def test_limite_de_paginas(self, settings, sem_espera) -> None:
        log: list[httpx.Request] = []
        with _client(settings, _fake_site_and_api(log), sem_espera) as client:
            resultado = find_equipment(
                client, parse_need("resistência a dragão"), SearchOptions(categories=("armor",), max_pages=1)
            )
        assert resultado["candidatos"] == 4
        assert any("mais páginas" in nota for nota in resultado["notas"])

    def test_filtro_de_slots(self, settings, sem_espera) -> None:
        log: list[httpx.Request] = []
        with _client(settings, _fake_site_and_api(log), sem_espera) as client:
            resultado = find_equipment(
                client,
                parse_need("resistência a dragão"),
                SearchOptions(categories=("armor",), min_slots=2, details=False),
            )
        assert resultado["candidatos"] == 0
        assert resultado["excluidos"]["sem_slots_suficientes"] == 6

    def test_site_fora_da_base_latam_e_erro(self, settings, sem_espera) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_listing_html(PAGINA_1, total=6, region="dpRO", language="English"))

        with _client(settings, handler, sem_espera) as client:
            with pytest.raises(SourceError, match="LATAM"):
                find_equipment(client, parse_need("resistência a dragão"))

    def test_sem_chave_falha_so_na_api(self, settings, sem_espera) -> None:
        sem_chave = settings.__class__(divine_pride_api_key=None, cache_dir=settings.cache_dir)
        log: list[httpx.Request] = []
        with _client(sem_chave, _fake_site_and_api(log), sem_espera) as client:
            listagem = find_equipment(client, parse_need("resistência a dragão"), SearchOptions(details=False))
            assert listagem["candidatos"] == 6
            with pytest.raises(ConfigError, match="DIVINE_PRIDE_API_KEY"):
                find_equipment(client, parse_need("resistência a dragão"))


class TestResolucaoDeFiltros:
    def test_categorias(self) -> None:
        assert needs.resolve_categories(None) == ("armor", "weapon")
        assert needs.resolve_categories(["arma", "Armadura", "cartas", "weapon"]) == ("weapon", "armor", "card")
        with pytest.raises(ValueError):
            needs.resolve_categories(["poção"])

    def test_subtipos(self) -> None:
        assert needs.resolve_sub_types(["capa", "Bota", "espada de duas mãos", "Accessory"]) == (
            "Garment", "Shoes", "Two-handed Sword", "Accessory",
        )

    def test_classes(self) -> None:
        assert needs.resolve_job_ids(["Cavaleiro Rúnico", "Dragon Knight", "rune knight"]) == (4054, 4252)
        with pytest.raises(ValueError, match="Classe desconhecida"):
            needs.resolve_job_ids(["Pokémon"])


class TestIntegracoes:
    def test_ferramenta_mcp(self, settings, sem_espera, monkeypatch) -> None:
        log: list[httpx.Request] = []
        monkeypatch.setattr(
            mcp_server, "DivinePrideClient", lambda: _client(settings, _fake_site_and_api(log), sem_espera)
        )
        out = mcp_server.buscar_equipamento_por_necessidade(
            tipo="resistencia_raca", alvo="dragão", categorias=["armadura"]
        )
        assert out["ok"] is True
        assert [i["nome"] for i in out["itens"]] == ["Elmo do Dragão [1]", "Sopro do Dragão [1]", "Bota Dracônica [1]"]

    def test_ferramenta_mcp_sem_argumentos_devolve_vocabulario(self, settings) -> None:
        out = mcp_server.buscar_equipamento_por_necessidade()
        assert out["ok"] is False and "tipos" in out["vocabulario"]
        assert mcp_server.vocabulario_de_necessidades()["ok"] is True

    def test_ferramenta_mcp_alvo_invalido(self, settings) -> None:
        out = mcp_server.buscar_equipamento_por_necessidade(tipo="resistencia_raca", alvo="poring")
        assert out["ok"] is False and "Opções" in out["erro"]

    def test_cli_find(self, settings, sem_espera, monkeypatch) -> None:
        log: list[httpx.Request] = []
        monkeypatch.setattr(cli, "DivinePrideClient", lambda: _client(settings, _fake_site_and_api(log), sem_espera))
        runner = CliRunner()
        resultado = runner.invoke(cli.app, ["find", "resistência a dragão", "-c", "armadura"])
        assert resultado.exit_code == 0, resultado.output
        assert "Elmo do Dragão" in resultado.output
        assert "Possíveis" in resultado.output and "Cota Dracônica Azul" in resultado.output

        em_json = runner.invoke(cli.app, ["find", "resistência a dragão", "-c", "armadura", "--json"])
        assert em_json.exit_code == 0, em_json.output
        assert '"servidor": "LATAM"' in em_json.output

    def test_cli_find_necessidade_invalida(self, settings) -> None:
        resultado = CliRunner().invoke(cli.app, ["find", "quero ficar bonito"])
        assert resultado.exit_code == 1
