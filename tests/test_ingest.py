"""Testes de ingestão: YAML/JSON, texto solto, schema e resolução de classe."""

from __future__ import annotations

import json

import pytest

from ragdata import ingest
from ragdata.errors import RagdataError
from ragdata.jobs import canonical_job_key, display_name, normalize, suggest_jobs
from ragdata.models import EquipSlot, WeaponType

YAML_EXEMPLO = """
name: Kaya
job: Cavaleiro Rúnico
base_level: 175
job_level: 60
goal: mvp
stats: { str: 120, agi: 90, vit: 80, int: 40, dex: 90, luk: 30 }
equipment:
  - slot: right_hand
    name: Espada Rúnica
    weapon_type: 2hSword
    weapon_level: 4
    base_atk: 220
    refine: 10
    slots: 2
    cards:
      - name: Carta Hydra
        note: "+20% em Demi-Humano"
"""


class TestCarregamento:
    def test_yaml(self) -> None:
        character = ingest.load_character_text(YAML_EXEMPLO)
        assert character.name == "Kaya"
        assert character.base_level == 175
        assert character.stats.str_ == 120
        assert character.weapon_type is WeaponType.SWORD_2H
        assert character.right_hand.cards[0].note

    def test_json(self) -> None:
        payload = json.dumps(
            {"job": "Sura", "base_level": 180, "job_level": 50, "stats": {"str": 100}}
        )
        character = ingest.load_character_text(payload)
        assert character.job == "Sura"
        assert character.stats.str_ == 100

    def test_arquivo(self, tmp_path) -> None:
        caminho = tmp_path / "p.yaml"
        caminho.write_text(YAML_EXEMPLO, encoding="utf-8")
        assert ingest.load_character_file(str(caminho)).name == "Kaya"

    def test_vazio_falha(self) -> None:
        with pytest.raises(RagdataError, match="vazio"):
            ingest.load_character_text("   ")

    def test_yaml_invalido_falha_com_mensagem(self) -> None:
        with pytest.raises(RagdataError):
            ingest.load_character_text("job: [não fecha")

    def test_lista_no_topo_falha(self) -> None:
        with pytest.raises(RagdataError, match="mapa"):
            ingest.load_character_text("- a\n- b")

    def test_erro_de_validacao_aponta_o_campo(self) -> None:
        with pytest.raises(RagdataError) as exc:
            ingest.character_from_payload({"job": "Sura", "base_level": 9999})
        assert "base_level" in str(exc.value)

    def test_campo_desconhecido_e_recusado(self) -> None:
        with pytest.raises(RagdataError):
            ingest.character_from_payload({"job": "Sura", "campo_inventado": 1})

    def test_dois_itens_no_mesmo_slot_falha(self) -> None:
        payload = {
            "job": "Sura",
            "equipment": [
                {"slot": "armor", "name": "A"},
                {"slot": "armor", "name": "B"},
            ],
        }
        with pytest.raises(RagdataError, match="posição"):
            ingest.character_from_payload(payload)

    def test_cartas_alem_dos_slots_falha(self) -> None:
        payload = {
            "job": "Sura",
            "equipment": [
                {
                    "slot": "armor",
                    "name": "A",
                    "slots": 1,
                    "cards": [{"name": "c1"}, {"name": "c2"}],
                }
            ],
        }
        with pytest.raises(RagdataError):
            ingest.character_from_payload(payload)


class TestTextoSolto:
    def test_extrai_atributos_e_niveis(self) -> None:
        out = ingest.parse_freeform(
            "Cavaleiro Rúnico base 175 job 60, STR 120 AGI 90 VIT 80 INT 40 DEX 90 LUK 30, quero mvp"
        )
        assert out["stats"] == {"str": 120, "agi": 90, "vit": 80, "int": 40, "dex": 90, "luk": 30}
        assert out["base_level"] == 175
        assert out["job_level"] == 60
        assert out["goal"] == "mvp"
        assert out["job"] == "cavaleiro runico"

    def test_rotulos_por_extenso(self) -> None:
        out = ingest.parse_freeform("nível base 185, nível de classe 65, força 100, destreza 90")
        assert out["base_level"] == 185
        assert out["job_level"] == 65
        assert out["stats"]["str"] == 100
        assert out["stats"]["dex"] == 90

    def test_nao_inventa_o_que_nao_achou(self) -> None:
        out = ingest.parse_freeform("um personagem qualquer")
        assert "stats" not in out
        assert "base_level" not in out

    def test_prefere_o_nome_de_classe_mais_longo(self) -> None:
        out = ingest.parse_freeform("meu cavaleiro rúnico está fraco")
        assert out["job"] == "cavaleiro runico"


class TestSchemaEInstrucoes:
    def test_schema_tem_os_campos_principais(self) -> None:
        schema = ingest.character_schema()
        assert "job" in schema["properties"]
        assert "equipment" in schema["properties"]

    def test_instrucoes_avisam_sobre_base_vs_total(self) -> None:
        texto = ingest.screenshot_instructions()
        assert "base" in texto and "total" in texto.lower()
        # Todos os slots e tipos de arma precisam estar listados para o modelo.
        for slot in EquipSlot:
            assert slot.value in texto
        for arma in WeaponType:
            assert arma.value in texto


class TestResolucaoDeClasse:
    KNOWN = {"Rune_Knight", "Rune_Knight_T", "Arch_Bishop", "Sura", "Knight", "Swordman"}

    def test_chave_direta(self) -> None:
        assert canonical_job_key("Rune_Knight", known=self.KNOWN) == "Rune_Knight"

    def test_nome_em_portugues(self) -> None:
        assert canonical_job_key("Cavaleiro Rúnico", known=self.KNOWN) == "Rune_Knight"

    def test_ignora_acento_e_caixa(self) -> None:
        assert canonical_job_key("cavaleiro runico", known=self.KNOWN) == "Rune_Knight"
        assert canonical_job_key("ARCEBISPO", known=self.KNOWN) == "Arch_Bishop"

    def test_variante_transclasse(self) -> None:
        assert canonical_job_key("Cavaleiro Rúnico Trans", known=self.KNOWN) == "Rune_Knight_T"

    def test_trans_inexistente_cai_para_a_base(self) -> None:
        assert canonical_job_key("Sura trans", known=self.KNOWN) == "Sura"

    def test_desconhecida_devolve_none(self) -> None:
        assert canonical_job_key("Pokemon", known=self.KNOWN) is None

    def test_vazio_devolve_none(self) -> None:
        assert canonical_job_key("   ", known=self.KNOWN) is None

    def test_sugestoes_para_erro_de_digitacao(self) -> None:
        assert "Sura" in suggest_jobs("Suraa", known=self.KNOWN)

    def test_normalize(self) -> None:
        assert normalize("Cavaleiro  Rúnico") == "cavaleiro runico"
        assert normalize("Rune_Knight") == "rune knight"

    def test_display_name(self) -> None:
        assert display_name("Rune_Knight").lower().startswith("cavaleiro")
        assert display_name("Classe_Inventada") == "Classe Inventada"
