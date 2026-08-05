"""Testes da camada de análise."""

from __future__ import annotations

import pytest

from ragdata import analysis
from ragdata.models import (
    BaseStats,
    Character,
    Element,
    Equipment,
    EquipSlot,
    Goal,
    Race,
    TargetMonster,
    WeaponType,
)


def _titulos(findings) -> str:
    return " | ".join(f.title for f in findings)


class TestAuditoriaDePontos:
    def test_build_coerente(self, settings, rune_knight) -> None:
        audit = analysis.stat_point_audit(rune_knight, settings)
        assert audit.consistent
        assert audit.spent > 0
        assert audit.remaining == audit.available - audit.spent

    def test_detecta_stats_totais_informados_como_base(self, settings) -> None:
        """O erro mais comum ao ler print: somar base + bônus."""
        character = Character(
            job="Rune_Knight",
            base_level=15,
            job_level=10,
            stats=BaseStats(**{"str": 130, "agi": 130, "vit": 130, "int": 130, "dex": 130, "luk": 130}),
        )
        audit = analysis.stat_point_audit(character, settings)
        assert not audit.consistent

        report = analysis.analyze(character, settings=settings)
        criticos = [f for f in report.findings if f.severity == "critico"]
        assert any("Mais pontos gastos" in f.title for f in criticos)

    def test_custo_por_atributo_e_registrado(self, settings, rune_knight) -> None:
        audit = analysis.stat_point_audit(rune_knight, settings)
        assert set(audit.per_stat) == {"str", "agi", "vit", "int", "dex", "luk"}
        assert audit.per_stat["str"] > audit.per_stat["luk"]


class TestBreakpoints:
    def test_encontra_o_proximo_ponto_de_aspd(self, settings, rune_knight) -> None:
        from ragdata.engine import compute

        derived = compute(rune_knight, settings)
        bps = analysis.find_breakpoints(rune_knight, derived, settings)
        aspd = [b for b in bps if b.stat == "agi"]
        assert aspd, "deveria existir um próximo breakpoint de ASPD"
        assert aspd[0].to_value > aspd[0].from_value
        assert aspd[0].cost_points > 0

    def test_sem_breakpoint_de_aspd_no_teto(self, settings) -> None:
        from ragdata.engine import compute

        character = Character(
            job="Rune_Knight",
            base_level=200,
            job_level=70,
            stats=BaseStats(agi=130, dex=130),
            equipment=[
                Equipment(
                    slot=EquipSlot.RIGHT_HAND,
                    name="Adaga",
                    weapon_type=WeaponType.DAGGER,
                    weapon_level=4,
                    base_atk=100,
                )
            ],
        )
        derived = compute(character, settings)
        if derived.aspd >= derived.aspd_cap:
            bps = analysis.find_breakpoints(character, derived, settings)
            assert not [b for b in bps if b.stat == "agi"]

    def test_breakpoint_de_conjuracao(self, settings) -> None:
        from ragdata.engine import compute

        character = Character(
            job="Rune_Knight",
            base_level=150,
            job_level=50,
            stats=BaseStats(**{"dex": 60, "int": 60}),
        )
        derived = compute(character, settings)
        bps = analysis.find_breakpoints(character, derived, settings)
        cast = [b for b in bps if "conjuração" in b.effect]
        assert cast, "deveria sugerir como reduzir mais o cast"
        assert all(b.cost_points > 0 for b in cast)
        assert all(b.to_value <= analysis.stat_cap("Rune_Knight") for b in cast)

    def test_nao_promete_cast_instantaneo_so_com_atributos(self, settings) -> None:
        """O teto de 130 limita DEX*2+INT a 390, abaixo dos 530 do instantâneo."""
        from ragdata.engine import compute

        character = Character(
            job="Rune_Knight",
            base_level=200,
            job_level=70,
            stats=BaseStats(**{"dex": 130, "int": 130}),
        )
        derived = compute(character, settings)
        assert not derived.instant_cast
        bps = analysis.find_breakpoints(character, derived, settings)
        assert not [b for b in bps if "instantân" in b.effect]


class TestValidacao:
    def test_arma_de_duas_maos_com_escudo(self, settings, rune_knight) -> None:
        invalido = rune_knight.model_copy(deep=True)
        invalido.equipment.append(
            Equipment(slot=EquipSlot.LEFT_HAND, name="Escudo", base_def=60)
        )
        report = analysis.analyze(invalido, settings=settings)
        assert any("duas mãos" in f.title for f in report.findings)

    def test_sem_arma_e_critico(self, settings) -> None:
        character = Character(job="Rune_Knight", base_level=100, job_level=50)
        report = analysis.analyze(character, settings=settings)
        assert any(f.severity == "critico" and "arma" in f.title.lower() for f in report.findings)

    def test_slots_de_carta_vazios_sao_apontados(self, settings, rune_knight) -> None:
        report = analysis.analyze(rune_knight, settings=settings)
        assert any("slot(s) de carta" in f.title for f in report.findings)

    def test_atributo_acima_do_teto(self, settings) -> None:
        character = Character(
            job="Rune_Knight",
            base_level=200,
            job_level=70,
            stats=BaseStats(**{"str": 200}),
        )
        report = analysis.analyze(character, settings=settings)
        assert any("acima do teto" in f.title for f in report.findings)

    def test_nivel_de_classe_incompleto_lista_o_que_falta(self, settings, rune_knight) -> None:
        report = analysis.analyze(rune_knight, settings=settings)
        dica = [f for f in report.findings if "níveis de classe" in f.title]
        assert dica and dica[0].gain


class TestSugestoes:
    def test_objetivo_ausente_avisa(self, settings, rune_knight) -> None:
        report = analysis.analyze(rune_knight, settings=settings)
        assert any("Sem objetivo declarado" in f.title for f in report.findings)

    def test_suporte_sem_cast_instantaneo_e_critico(self, settings) -> None:
        character = Character(
            job="Rune_Knight",
            base_level=150,
            job_level=50,
            stats=BaseStats(**{"int": 60, "dex": 60}),
            equipment=[
                Equipment(
                    slot=EquipSlot.RIGHT_HAND,
                    name="Cajado",
                    weapon_type=WeaponType.STAFF,
                    weapon_level=3,
                    base_atk=40,
                )
            ],
        )
        report = analysis.analyze(character, goal=Goal.SUPPORT, settings=settings)
        assert any(
            f.severity == "critico" and "conjuração instantânea" in f.title
            for f in report.findings
        )

    def test_ordenacao_por_severidade(self, settings) -> None:
        character = Character(job="Rune_Knight", base_level=100, job_level=50)
        report = analysis.analyze(character, goal=Goal.MVP, settings=settings)
        severidades = [f.severity for f in report.findings]
        ordem = {"critico": 0, "aviso": 1, "dica": 2}
        assert severidades == sorted(severidades, key=lambda s: ordem[s])

    def test_alvo_gera_comparacao_de_hit(self, settings, rune_knight) -> None:
        alvo = TargetMonster(
            name="Baphomet",
            monster_id=1039,
            flee=380,
            hit=290,
            race=Race.DEMON,
            element=Element.SHADOW,
            element_level=4,
            defense=140,
            is_mvp=True,
        )
        report = analysis.analyze(rune_knight, goal=Goal.MVP, target=alvo, settings=settings)
        titulos = _titulos(report.findings)
        assert "Chance de acerto em Baphomet" in titulos
        assert "raça demon" in titulos
        assert "DEF alta" in titulos


class TestSimulacao:
    def test_mudanca_de_atributo_muda_os_numeros(self, settings, rune_knight) -> None:
        resultado = analysis.simulate(rune_knight, {"stats": {"agi": 110}}, settings)
        assert "flee" in resultado["diferencas"]
        assert resultado["diferencas"]["flee"]["delta"] == 20
        assert resultado["pontos"]["depois"]["gastos"] > resultado["pontos"]["antes"]["gastos"]

    def test_mudanca_de_refino(self, settings, rune_knight) -> None:
        resultado = analysis.simulate(rune_knight, {"refine": {"Espada Rúnica": 15}}, settings)
        assert resultado["diferencas"]["atk_right"]["delta"] > 0

    def test_refino_por_slot(self, settings, rune_knight) -> None:
        resultado = analysis.simulate(rune_knight, {"refine": {"right_hand": 12}}, settings)
        assert resultado["diferencas"]["atk_right"]["delta"] > 0

    def test_item_inexistente_falha_claro(self, settings, rune_knight) -> None:
        with pytest.raises(ValueError, match="não encontrado"):
            analysis.simulate(rune_knight, {"refine": {"Espada Que Não Existe": 10}}, settings)

    def test_build_impossivel_avisa(self, settings, rune_knight) -> None:
        resultado = analysis.simulate(rune_knight, {"base_level": 30}, settings)
        assert resultado["aviso"] is not None
        assert not resultado["pontos"]["consistente"]

    def test_sem_mudanca_util_devolve_diff_vazio(self, settings, rune_knight) -> None:
        atual = rune_knight.stats.agi
        resultado = analysis.simulate(rune_knight, {"stats": {"agi": atual}}, settings)
        assert resultado["diferencas"] == {}


class TestRelatorio:
    def test_to_dict_e_serializavel(self, settings, rune_knight) -> None:
        import json

        report = analysis.analyze(rune_knight, goal=Goal.FARM, settings=settings)
        payload = report.to_dict()
        assert json.loads(json.dumps(payload, ensure_ascii=False))
        assert payload["personagem"]["classe"] == "Rune_Knight"
        assert payload["pontos"]["consistente"] is True
        assert isinstance(payload["achados"], list)
