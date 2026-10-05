"""Interface web local: rotas, parâmetros e degradação sem chave (tudo offline)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import httpx
import pytest
from test_needs import _client, _fake_site_and_api

from ragdata.config import Settings, set_settings
from ragdata.web import make_server, page_html


@pytest.fixture
def servidor(settings, sem_espera) -> Iterator[str]:
    log: list[httpx.Request] = []
    server = make_server("127.0.0.1", 0, client_factory=lambda: _client(settings, _fake_site_and_api(log), sem_espera))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.url
    finally:
        server.shutdown()
        server.server_close()
        server.client.close()


def _get(url: str) -> tuple[int, dict | str]:
    try:
        with urlopen(url, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        status = exc.code
    return status, (json.loads(body) if body.startswith("{") else body)


class TestRotas:
    def test_pagina(self, servidor: str) -> None:
        status, body = _get(servidor)
        assert status == 200
        assert body == page_html()
        assert "busca por necessidade" in body

    def test_estado_e_vocabulario(self, servidor: str) -> None:
        status, estado = _get(servidor + "api/estado")
        assert status == 200
        assert estado["servidor"] == "LATAM" and estado["api_key_configurada"] is True
        status, vocab = _get(servidor + "api/vocabulario")
        assert status == 200 and "resistencia_raca" in vocab["tipos"]

    def test_rota_desconhecida(self, servidor: str) -> None:
        status, body = _get(servidor + "nada")
        assert status == 404 and body["ok"] is False


class TestBusca:
    def test_texto_livre(self, servidor: str) -> None:
        params = {"necessidade": "resistência a dragão", "categoria": "armadura"}
        status, r = _get(servidor + "api/find?" + urlencode(params))
        assert status == 200 and r["ok"] is True
        assert [i["nome"] for i in r["itens"]] == ["Elmo do Dragão [1]", "Sopro do Dragão [1]", "Bota Dracônica [1]"]
        assert r["categorias"] == ["armor"]

    def test_estruturado_com_filtros(self, servidor: str) -> None:
        params = [
            ("tipo", "resistencia_raca"), ("alvo", "dragao"), ("categoria", "armadura"),
            ("subtipo", "capa"), ("classe", "Cavaleiro Rúnico"), ("nivel_min", "10"), ("nivel_max", "200"),
            ("slots_min", "1"), ("limite", "2"), ("paginas", "1"), ("detalhes", "1"),
        ]
        status, r = _get(servidor + "api/find?" + urlencode(params))
        assert status == 200, r
        assert r["necessidade"]["tipo"] == "resistencia_raca"
        assert r["filtros"] == {
            "subtipos": ["Garment"], "classes_divine_pride": [4054], "nivel_min": 10, "nivel_max": 200, "slots_min": 1,
        }
        assert r["consultados"] == 2

    def test_sem_detalhes(self, servidor: str) -> None:
        status, r = _get(servidor + "api/find?" + urlencode({"necessidade": "resistência a dragão", "detalhes": "0"}))
        assert status == 200 and r["consultados"] == 0 and r["itens"] == []
        assert len(r["nao_consultados"]) == 6

    def test_sem_parametros(self, servidor: str) -> None:
        status, r = _get(servidor + "api/find")
        assert status == 400 and r["ok"] is False and "vocabulario" in r

    def test_necessidade_invalida(self, servidor: str) -> None:
        status, r = _get(servidor + "api/find?" + urlencode({"necessidade": "quero ficar bonito"}))
        assert status == 400 and "Não entendi" in r["erro"]

    def test_classe_invalida(self, servidor: str) -> None:
        params = {"necessidade": "resistência a dragão", "classe": "Pokémon"}
        status, r = _get(servidor + "api/find?" + urlencode(params))
        assert status == 400 and "Classe desconhecida" in r["erro"]

    def test_sem_chave_cai_para_listagem(self, servidor: str, settings) -> None:
        set_settings(Settings(divine_pride_api_key=None, cache_dir=settings.cache_dir))
        try:
            status, estado = _get(servidor + "api/estado")
            assert estado["api_key_configurada"] is False
            params = {"necessidade": "resistência a dragão", "categoria": "armadura"}
            status, r = _get(servidor + "api/find?" + urlencode(params))
        finally:
            set_settings(settings)
        assert status == 200 and r["ok"] is True
        assert r["consultados"] == 0 and len(r["nao_consultados"]) == 6
        assert any("DIVINE_PRIDE_API_KEY" in nota for nota in r["notas"])
