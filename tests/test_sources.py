"""Testes dos clientes externos, com HTTP falso — nada sai para a rede."""

from __future__ import annotations

import httpx
import pytest

from ragdata.cache import Cache
from ragdata.errors import ConfigError, NotFound, SourceError, WrongRegion
from ragdata.models import Element, Race, Size
from ragdata.ratelimit import RateLimiter
from ragdata.sources.browiki import BrowikiClient
from ragdata.sources.divinepride import (
    DivinePrideClient,
    monster_to_target,
    normalize_item,
    normalize_monster,
)

ITEM_PAYLOAD = {
    "id": 1201,
    "name": "Adaga",
    "aegisName": "Knife",
    "description": "Uma adaga simples.",
    "slots": 3,
    "attack": 17,
    "defense": 0,
    "weight": 40,
    "requiredLevel": 1,
    "weaponLevel": 1,
    "itemTypeId": 4,
    "itemSubTypeId": 2,
    "location": 2,
}

MONSTER_PAYLOAD = {
    "id": 1039,
    "name": "Baphomet",
    "level": 81,
    "stats": {
        "health": 668000,
        "attack": {"minimum": 1200, "maximum": 1800},
        "defense": 140,
        "magicDefense": 60,
        "hit": 290,
        "flee": 380,
        "race": 6,
        "element": 87,
        "scale": 2,
        "mvp": True,
    },
}


def _fake_client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://www.divine-pride.net",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def sem_espera() -> RateLimiter:
    """Limitador que não dorme de verdade, mas registra que foi acionado."""
    chamadas: list[float] = []
    tempo = {"agora": 0.0}

    def sleep(segundos: float) -> None:
        chamadas.append(segundos)
        tempo["agora"] += segundos

    limiter = RateLimiter(1.0, sleep=sleep, monotonic=lambda: tempo["agora"])
    limiter.chamadas = chamadas  # type: ignore[attr-defined]
    return limiter


class TestNormalizacao:
    def test_item(self) -> None:
        data = normalize_item(ITEM_PAYLOAD)
        assert data["id"] == 1201
        assert data["name"] == "Adaga"
        assert data["attack"] == 17
        assert data["slots"] == 3
        assert data["raw"] is ITEM_PAYLOAD

    def test_item_com_nomes_alternativos(self) -> None:
        data = normalize_item({"itemId": 501, "unidName": "Poção", "atk": 0, "slot": 0})
        assert data["id"] == 501
        assert data["name"] == "Poção"

    def test_campo_ausente_vira_none(self) -> None:
        assert normalize_item({"id": 1})["attack"] is None

    def test_monstro_le_campos_aninhados(self) -> None:
        data = normalize_monster(MONSTER_PAYLOAD)
        assert data["hp"] == 668000
        assert data["defense"] == 140
        assert data["flee"] == 380

    def test_alvo_decodifica_raca_elemento_e_tamanho(self) -> None:
        alvo = monster_to_target(normalize_monster(MONSTER_PAYLOAD))
        assert alvo.name == "Baphomet"
        assert alvo.race is Race.DEMON
        # 87 = nível 4 * 20 + 7 (sombra)
        assert alvo.element is Element.SHADOW
        assert alvo.element_level == 4
        assert alvo.size is Size.LARGE
        assert alvo.is_mvp is True

    def test_alvo_tolera_dados_faltando(self) -> None:
        alvo = monster_to_target(normalize_monster({"id": 1002, "name": "Poring"}))
        assert alvo.name == "Poring"
        assert alvo.race is None
        assert alvo.is_mvp is False


class TestDivinePrideClient:
    def test_busca_item_e_guarda_em_cache(self, settings, sem_espera) -> None:
        chamadas = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            chamadas["n"] += 1
            assert request.url.params["apiKey"] == "chave-de-teste"
            # A API atual escolhe a região pelo header; a query `server` é o contrato antigo.
            assert request.headers["x-server"] == "LATAM"
            assert request.headers["accept-language"] == "pt"
            assert request.url.params["server"] == "LATAM"
            return httpx.Response(200, json={**ITEM_PAYLOAD, "region": "LATAM"})

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with DivinePrideClient(
            settings, client=_fake_client(handler), cache=cache, limiter=sem_espera
        ) as client:
            primeiro = client.item(1201)
            segundo = client.item(1201)

        assert primeiro["name"] == "Adaga"
        assert segundo["name"] == "Adaga"
        assert chamadas["n"] == 1, "a segunda chamada deveria vir do cache"

    def test_respeita_o_limite_de_uma_por_segundo(self, settings, sem_espera) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=ITEM_PAYLOAD)

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with DivinePrideClient(
            settings, client=_fake_client(handler), cache=cache, limiter=sem_espera
        ) as client:
            client.item(1201)
            client.item(1202)
            client.item(1203)

        # A primeira passa direto; as seguintes esperam ~1s cada.
        assert sem_espera.chamadas == [pytest.approx(1.0), pytest.approx(1.0)]

    def test_404_vira_not_found(self, settings, sem_espera) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with DivinePrideClient(
            settings, client=_fake_client(handler), cache=cache, limiter=sem_espera
        ) as client:
            with pytest.raises(NotFound):
                client.item(999999)

    def test_401_orienta_sobre_a_chave(self, settings, sem_espera) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with DivinePrideClient(
            settings, client=_fake_client(handler), cache=cache, limiter=sem_espera
        ) as client:
            with pytest.raises(ConfigError, match="DIVINE_PRIDE_API_KEY"):
                client.item(1201)

    def test_429_e_reportado(self, settings, sem_espera) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with DivinePrideClient(
            settings, client=_fake_client(handler), cache=cache, limiter=sem_espera
        ) as client:
            with pytest.raises(SourceError, match="1 req/s"):
                client.item(1201)

    def test_429_curto_espera_e_repete(self, settings, sem_espera) -> None:
        respostas = [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json=ITEM_PAYLOAD)]
        dormidas: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return respostas.pop(0)

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with DivinePrideClient(
            settings, client=_fake_client(handler), cache=cache, limiter=sem_espera, sleep=dormidas.append
        ) as client:
            assert client.item(1201)["name"] == "Adaga"

        assert dormidas == [2.0]
        assert respostas == []

    def test_429_longo_nao_espera(self, settings, sem_espera) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "600"})

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with DivinePrideClient(
            settings, client=_fake_client(handler), cache=cache, limiter=sem_espera,
            sleep=lambda s: pytest.fail("não deveria dormir 10 minutos"),
        ) as client:
            with pytest.raises(SourceError, match="600 s"):
                client.item(1201)

    def test_regiao_diferente_da_pedida_e_erro(self, settings, sem_espera) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={**ITEM_PAYLOAD, "region": "kROM"})

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with DivinePrideClient(
            settings, client=_fake_client(handler), cache=cache, limiter=sem_espera
        ) as client:
            with pytest.raises(WrongRegion, match="kROM") as info:
                client.item(1201)
            assert info.value.expected == "LATAM"
            # Nada de outra região vai para o cache.
            assert cache.get_json("dp:LATAM:pt:Item:1201") is None

    def test_servidor_e_idioma_por_chamada(self, settings, sem_espera) -> None:
        vistos: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            vistos.append((request.headers["x-server"], request.headers["accept-language"]))
            return httpx.Response(200, json={**ITEM_PAYLOAD, "region": request.headers["x-server"]})

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with DivinePrideClient(
            settings, client=_fake_client(handler), cache=cache, limiter=sem_espera
        ) as client:
            client.item(1201, server="bRO", language="en")
            client.item(1201)  # LATAM/pt: chave de cache diferente → nova chamada

        assert vistos == [("bRO", "en"), ("LATAM", "pt")]

    def test_campos_novos_da_api_sao_normalizados(self) -> None:
        payload = {
            "id": 501, "name": "Poção Vermelha", "aegisName": "Red_Potion", "type": "Healing", "subType": "",
            "description": "Uma poção.", "sellPrice": 25, "buyPrice": 50, "weight": 70,
            "allJobsAllowed": True, "allowedJobIds": [], "sources": [], "scripts": [], "region": "LATAM",
        }
        data = normalize_item(payload)
        assert data["type"] == "Healing" and data["sub_type"] is None
        assert data["region"] == "LATAM" and data["sell_price"] == 25
        assert data["allowed_job_ids"] == []

    def test_monstro_da_api_atual_por_nome(self) -> None:
        alvo = monster_to_target(normalize_monster({
            "id": 1002, "name": "Poring", "level": 1, "race": "Plant", "element": "Water", "elementLevel": 1,
            "size": "Medium", "type": "Normal", "health": 50, "def": 0, "mDef": 5, "hit": 0, "flee": 0,
        }))
        assert alvo.race is Race.PLANT and alvo.element is Element.WATER and alvo.element_level == 1
        assert alvo.size is Size.MEDIUM and alvo.magic_defense == 5 and alvo.is_mvp is False

    def test_sem_chave_falha_antes_da_rede(self, settings, sem_espera) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("não deveria chamar a rede sem chave")

        sem_chave = settings.__class__(
            divine_pride_api_key=None, cache_dir=settings.cache_dir
        )
        cache = Cache(sem_chave.http_cache_path, sem_chave.cache_ttl_seconds)
        with DivinePrideClient(
            sem_chave, client=_fake_client(handler), cache=cache, limiter=sem_espera
        ) as client:
            with pytest.raises(ConfigError, match="Falta a chave"):
                client.item(1201)

    def test_busca_por_nome_extrai_os_links(self, settings, sem_espera) -> None:
        html = """
        <table><tr><td><a href="/database/item/1201">Adaga</a></td></tr>
          <tr><td><a href="/database/item/1202">Adaga+</a></td></tr>
          <tr><td><a href="/database/item/7607">(null)</a></td></tr>
          <tr><td><a href="/database/monster/1002/poring">Poring</a></td></tr></table>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/database"
            assert request.url.params["q"] == "adaga"
            assert "includeDescription" not in request.url.params
            assert request.headers["accept-language"] == "pt-BR,pt;q=0.9"
            return httpx.Response(200, text=html)

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with DivinePrideClient(
            settings, client=_fake_client(handler), cache=cache, limiter=sem_espera
        ) as client:
            itens = client.search("adaga", kind="item")
            monstros = client.search("adaga", kind="monster")

        assert [i["id"] for i in itens] == [1201, 1202]
        assert [m["id"] for m in monstros] == [1002]

    def test_busca_com_html_inesperado_devolve_vazio(self, settings, sem_espera) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><body>nada aqui</body></html>")

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with DivinePrideClient(
            settings, client=_fake_client(handler), cache=cache, limiter=sem_espera
        ) as client:
            assert client.search("qualquer coisa") == []


class TestBrowikiClient:
    def test_le_pagina(self, settings) -> None:
        payload = {
            "query": {
                "pages": {
                    "42": {
                        "title": "Cavaleiro Rúnico",
                        "extract": "O Cavaleiro Rúnico é a terceira classe do Espadachim.",
                    }
                }
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["explaintext"] == "1"
            return httpx.Response(200, json=payload)

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with BrowikiClient(
            settings, client=httpx.Client(transport=httpx.MockTransport(handler)), cache=cache
        ) as client:
            page = client.page("Cavaleiro Rúnico")

        assert page["title"] == "Cavaleiro Rúnico"
        assert "terceira classe" in page["text"]
        assert page["url"].endswith("Cavaleiro_Rúnico")
        assert page["truncated"] is False

    def test_pagina_ausente_levanta_not_found(self, settings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"query": {"pages": {"-1": {"missing": ""}}}})

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with BrowikiClient(
            settings, client=httpx.Client(transport=httpx.MockTransport(handler)), cache=cache
        ) as client:
            with pytest.raises(NotFound):
                client.page("Página Que Não Existe")

    def test_busca_limpa_o_html_do_trecho(self, settings) -> None:
        payload = {
            "query": {
                "search": [
                    {
                        "title": "Sura",
                        "snippet": 'O <span class="searchmatch">Sura</span> usa &quot;punhos&quot;',
                        "wordcount": 120,
                    }
                ]
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with BrowikiClient(
            settings, client=httpx.Client(transport=httpx.MockTransport(handler)), cache=cache
        ) as client:
            hits = client.search("sura")

        assert hits[0]["snippet"] == 'O Sura usa "punhos"'

    def test_lookup_cai_para_a_busca(self, settings) -> None:
        respostas = [
            {"query": {"pages": {"-1": {"missing": ""}}}},
            {"query": {"search": [{"title": "Sura", "snippet": "", "wordcount": 1}]}},
            {"query": {"pages": {"7": {"title": "Sura", "extract": "Classe de punhos."}}}},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=respostas.pop(0))

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with BrowikiClient(
            settings, client=httpx.Client(transport=httpx.MockTransport(handler)), cache=cache
        ) as client:
            page = client.lookup("sura de punho")

        assert page["title"] == "Sura"

    def test_texto_e_truncado(self, settings) -> None:
        longo = "x" * 5000
        payload = {"query": {"pages": {"1": {"title": "T", "extract": longo}}}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with BrowikiClient(
            settings, client=httpx.Client(transport=httpx.MockTransport(handler)), cache=cache
        ) as client:
            page = client.page("T", max_chars=100)

        assert len(page["text"]) == 100
        assert page["truncated"] is True

    def test_json_invalido_vira_source_error(self, settings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="isto não é json")

        cache = Cache(settings.http_cache_path, settings.cache_ttl_seconds)
        with BrowikiClient(
            settings, client=httpx.Client(transport=httpx.MockTransport(handler)), cache=cache
        ) as client:
            with pytest.raises(SourceError):
                client.page("T")


class TestCache:
    def test_guarda_e_recupera(self, tmp_path) -> None:
        cache = Cache(tmp_path / "c.sqlite3", ttl_seconds=3600)
        cache.set("k", "valor")
        assert cache.get("k") == "valor"

    def test_expira(self, tmp_path) -> None:
        cache = Cache(tmp_path / "c.sqlite3", ttl_seconds=3600)
        cache.set("k", "valor")
        assert cache.get("k", ttl_seconds=0) is None

    def test_json(self, tmp_path) -> None:
        cache = Cache(tmp_path / "c.sqlite3", ttl_seconds=3600)
        cache.set_json("k", {"a": [1, 2]})
        assert cache.get_json("k") == {"a": [1, 2]}

    def test_json_corrompido_devolve_none(self, tmp_path) -> None:
        cache = Cache(tmp_path / "c.sqlite3", ttl_seconds=3600)
        cache.set("k", "{isto não é json")
        assert cache.get_json("k") is None

    def test_sobrescreve(self, tmp_path) -> None:
        cache = Cache(tmp_path / "c.sqlite3", ttl_seconds=3600)
        cache.set("k", "a")
        cache.set("k", "b")
        assert cache.get("k") == "b"

    def test_limpa_e_conta(self, tmp_path) -> None:
        cache = Cache(tmp_path / "c.sqlite3", ttl_seconds=3600)
        cache.set("a", "1")
        cache.set("b", "2")
        assert cache.stats()["total"] == 2
        assert cache.clear() == 2
        assert cache.stats()["total"] == 0

    def test_persiste_entre_instancias(self, tmp_path) -> None:
        caminho = tmp_path / "c.sqlite3"
        Cache(caminho, ttl_seconds=3600).set("k", "valor")
        assert Cache(caminho, ttl_seconds=3600).get("k") == "valor"


class TestRateLimiter:
    def test_primeira_chamada_nao_espera(self) -> None:
        tempo = {"agora": 0.0}
        limiter = RateLimiter(1.0, sleep=lambda s: None, monotonic=lambda: tempo["agora"])
        assert limiter.acquire() == 0.0

    def test_intervalo_zero_desliga(self) -> None:
        limiter = RateLimiter(0.0, sleep=lambda s: pytest.fail("não deveria dormir"))
        assert limiter.acquire() == 0.0

    def test_espera_o_intervalo_configurado(self) -> None:
        dormidas: list[float] = []
        tempo = {"agora": 0.0}

        def sleep(s: float) -> None:
            dormidas.append(s)
            tempo["agora"] += s

        limiter = RateLimiter(1.0, sleep=sleep, monotonic=lambda: tempo["agora"])
        limiter.acquire()
        limiter.acquire()
        assert dormidas == [pytest.approx(1.0)]

    def test_nao_espera_se_ja_passou_tempo(self) -> None:
        tempo = {"agora": 0.0}

        def sleep(s: float) -> None:  # pragma: no cover
            raise AssertionError("não deveria dormir")

        limiter = RateLimiter(1.0, sleep=sleep, monotonic=lambda: tempo["agora"])
        limiter.acquire()
        tempo["agora"] = 5.0
        assert limiter.acquire() == 0.0
