"""Testes do download de tabelas e das ferramentas MCP (tudo offline)."""

from __future__ import annotations

import httpx
import pytest
import yaml

from ragdata import mcp_server
from ragdata.config import RATHENA_TABLES, Settings
from ragdata.errors import GameDataMissing, SourceError
from ragdata.gamedata import (
    clear_job_cache,
    enchantgrade_bonus,
    refine_bonus,
    status_points_at,
)
from ragdata.setup_data import download_tables, missing_tables

PERSONAGEM = {
    "job": "Rune_Knight",
    "base_level": 175,
    "job_level": 60,
    "stats": {"str": 120, "agi": 90, "vit": 80, "int": 40, "dex": 90, "luk": 30},
    "equipment": [
        {
            "slot": "right_hand",
            "name": "Espada Rúnica",
            "weapon_type": "2hSword",
            "weapon_level": 4,
            "base_atk": 220,
            "refine": 10,
            "slots": 2,
        }
    ],
}


class TestDownloadDeTabelas:
    def test_baixa_todas(self, tmp_path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="Header: {}\nBody: []\n")

        cfg = Settings(cache_dir=tmp_path)
        client = httpx.Client(transport=httpx.MockTransport(handler))
        resultados = download_tables(cfg, client=client)

        assert len(resultados) == len(RATHENA_TABLES)
        assert all(not r.skipped for r in resultados)
        assert missing_tables(cfg) == []

    def test_pula_o_que_ja_existe(self, tmp_path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="Header: {}\nBody: []\n")

        cfg = Settings(cache_dir=tmp_path)
        client = httpx.Client(transport=httpx.MockTransport(handler))
        download_tables(cfg, client=client)
        segunda = download_tables(cfg, client=client)
        assert all(r.skipped for r in segunda)

    def test_force_rebaixa(self, tmp_path) -> None:
        chamadas = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            chamadas["n"] += 1
            return httpx.Response(200, text="Header: {}\nBody: []\n")

        cfg = Settings(cache_dir=tmp_path)
        client = httpx.Client(transport=httpx.MockTransport(handler))
        download_tables(cfg, client=client)
        download_tables(cfg, force=True, client=client)
        assert chamadas["n"] == 2 * len(RATHENA_TABLES)

    def test_erro_de_rede_e_reportado(self, tmp_path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        cfg = Settings(cache_dir=tmp_path)
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(SourceError, match="500"):
            download_tables(cfg, client=client)

    def test_lista_o_que_falta(self, tmp_path) -> None:
        cfg = Settings(cache_dir=tmp_path)
        assert set(missing_tables(cfg)) == set(RATHENA_TABLES)


class TestGameData:
    def test_tabela_ausente_orienta_o_setup(self, tmp_path) -> None:
        clear_job_cache()
        cfg = Settings(cache_dir=tmp_path)
        with pytest.raises(GameDataMissing, match="ragdata setup"):
            status_points_at(100, cfg)
        clear_job_cache()

    def test_arquivo_invalido_e_tratado(self, tmp_path) -> None:
        clear_job_cache()
        gamedata = tmp_path / "gamedata"
        gamedata.mkdir(parents=True)
        (gamedata / "statpoint.yml").write_text("só um texto solto", encoding="utf-8")
        cfg = Settings(cache_dir=tmp_path)
        with pytest.raises(GameDataMissing):
            status_points_at(100, cfg)
        clear_job_cache()

    def test_bonus_de_refino_de_arma(self, settings) -> None:
        # Fixture: bônus = refino * (nível_da_arma + 1)
        assert refine_bonus("Weapon", 4, 10, settings) == 50
        assert refine_bonus("Weapon", 4, 0, settings) == 0

    def test_bonus_de_refino_de_armadura(self, settings) -> None:
        assert refine_bonus("Armor", 1, 7, settings) == 7

    def test_grau_soma_percentual(self, settings) -> None:
        assert enchantgrade_bonus("Weapon", 4, 0, settings) == 0
        assert enchantgrade_bonus("Weapon", 4, 1, settings) == 30
        assert enchantgrade_bonus("Weapon", 4, 3, settings) == 100

    def test_pontos_acima_do_ultimo_nivel_tabelado(self, settings) -> None:
        maximo = status_points_at(200, settings)
        assert status_points_at(999, settings) == maximo

    def test_tabelas_reais_sao_yaml_valido(self, settings) -> None:
        """As fixtures precisam ter a mesma forma das tabelas do rAthena."""
        for nome in RATHENA_TABLES:
            caminho = settings.gamedata_dir / f"{nome}.yml"
            dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
            assert "Body" in dados


class TestFerramentasMCP:
    def test_estado(self, settings) -> None:
        estado = mcp_server.estado_do_ragdata()
        assert estado["ok"] is True
        assert estado["tabelas_faltando"] == []
        assert estado["pronto"] is True

    def test_schema(self, settings) -> None:
        out = mcp_server.schema_do_personagem()
        assert out["ok"] is True
        assert "base" in out["instrucoes"]
        assert "properties" in out["schema"]

    def test_analisar(self, settings) -> None:
        out = mcp_server.analisar_personagem(PERSONAGEM, objetivo="mvp")
        assert out["ok"] is True
        assert out["personagem"]["classe"] == "Rune_Knight"
        assert out["derivados"]["aspd"] > 0
        assert isinstance(out["achados"], list)

    def test_analisar_objetivo_invalido(self, settings) -> None:
        out = mcp_server.analisar_personagem(PERSONAGEM, objetivo="virar peixe")
        assert out["ok"] is False
        assert "erro" in out

    def test_analisar_personagem_invalido(self, settings) -> None:
        out = mcp_server.analisar_personagem({"job": "Rune_Knight", "base_level": -5})
        assert out["ok"] is False

    def test_classe_desconhecida(self, settings) -> None:
        out = mcp_server.analisar_personagem({**PERSONAGEM, "job": "Classe Que Não Existe"})
        assert out["ok"] is False
        assert "desconhecida" in out["erro"].lower()

    def test_calcular_stats(self, settings) -> None:
        out = mcp_server.calcular_stats(PERSONAGEM)
        assert out["ok"] is True
        assert "+" in out["atk"]
        assert out["hp_max"] > 0

    def test_simular(self, settings) -> None:
        out = mcp_server.simular_mudanca(PERSONAGEM, {"stats": {"agi": 110}})
        assert out["ok"] is True
        assert "flee" in out["diferencas"]

    def test_simular_mudanca_invalida(self, settings) -> None:
        out = mcp_server.simular_mudanca(PERSONAGEM, {"refine": {"Item Fantasma": 5}})
        assert out["ok"] is False

    def test_ler_texto(self, settings) -> None:
        out = mcp_server.ler_texto_de_personagem("Sura base 180 job 55 STR 110")
        assert out["ok"] is True
        assert out["rascunho"]["base_level"] == 180

    def test_buscar_item_sem_argumento(self, settings) -> None:
        out = mcp_server.buscar_item()
        assert out["ok"] is False
        assert "item_id" in out["erro"]

    def test_buscar_monstro_sem_argumento(self, settings) -> None:
        out = mcp_server.buscar_monstro()
        assert out["ok"] is False

    def test_tabelas_ausentes_bloqueiam_a_analise(self, tmp_path) -> None:
        from ragdata.config import set_settings

        clear_job_cache()
        set_settings(Settings(cache_dir=tmp_path))
        try:
            out = mcp_server.analisar_personagem(PERSONAGEM)
            assert out["ok"] is False
            assert "ragdata setup" in out["erro"]
        finally:
            clear_job_cache()
            set_settings(Settings.from_env())
