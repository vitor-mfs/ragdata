"""Testes do motor Renewal.

Os valores esperados são calculados à mão a partir das fórmulas do rAthena,
para que uma mudança acidental no código apareça como falha em vez de passar.
"""

from __future__ import annotations

import math

import pytest

from ragdata import engine
from ragdata.engine import TotalStats, status_atk, status_matk, variable_cast_reduction
from ragdata.gamedata import resolve_job, status_point_cost, status_points_spent
from ragdata.models import BaseStats, Character, Equipment, EquipSlot, WeaponType


def _stats(**kwargs: int) -> TotalStats:
    base = {"str_": 1, "agi": 1, "vit": 1, "int_": 1, "dex": 1, "luk": 1}
    base.update(kwargs)
    return TotalStats(**base)


class TestStatusAtk:
    def test_formula_corpo_a_corpo(self) -> None:
        # (120*10 + 90*10//5 + 30*10//3 + 175*10//4) // 10
        # = (1200 + 180 + 100 + 437) // 10 = 191
        stats = _stats(str_=120, dex=90, luk=30)
        assert status_atk(stats, 175, ranged=False) == 191

    def test_arma_de_longa_distancia_troca_str_e_dex(self) -> None:
        stats = _stats(str_=30, dex=120, luk=30)
        corpo = status_atk(stats, 175, ranged=False)
        distancia = status_atk(stats, 175, ranged=True)
        assert distancia > corpo
        # DEX vira o primário: (120*10 + 30*2 + 100 + 437)//10 = 179
        assert distancia == 179

    def test_pow_soma_cinco_por_ponto(self) -> None:
        sem = status_atk(_stats(str_=100), 100, ranged=False)
        com = status_atk(_stats(str_=100, pow=10), 100, ranged=False)
        assert com - sem == 50


class TestStatusMatk:
    def test_formula(self) -> None:
        # 120 + 60 + 90//5 + 30//3 + 175//4 = 120+60+18+10+43 = 251
        stats = _stats(int_=120, dex=90, luk=30)
        assert status_matk(stats, 175) == 251

    def test_spl_soma_cinco_por_ponto(self) -> None:
        assert status_matk(_stats(int_=100, spl=6), 100) - status_matk(_stats(int_=100), 100) == 30


class TestConjuracao:
    def test_instantaneo_em_530(self) -> None:
        assert variable_cast_reduction(_stats(dex=265, int_=0)) == 1.0
        assert variable_cast_reduction(_stats(dex=200, int_=130)) == 1.0

    def test_reducao_e_raiz_quadrada(self) -> None:
        stats = _stats(dex=100, int_=100)
        esperado = math.sqrt(300 / 530)
        assert variable_cast_reduction(stats) == pytest.approx(esperado)

    def test_monotonico(self) -> None:
        anterior = 0.0
        for dex in range(1, 260, 20):
            atual = variable_cast_reduction(_stats(dex=dex))
            assert atual >= anterior
            anterior = atual


class TestPontosDeAtributo:
    def test_custo_abaixo_de_100(self) -> None:
        # 2 + (n-1)//10
        assert status_point_cost(1) == 2
        assert status_point_cost(10) == 2
        assert status_point_cost(11) == 3
        assert status_point_cost(99) == 11

    def test_custo_a_partir_de_100(self) -> None:
        # 16 + 4*((n-100)//5)
        assert status_point_cost(100) == 16
        assert status_point_cost(104) == 16
        assert status_point_cost(105) == 20
        assert status_point_cost(130) == 40

    def test_gasto_acumulado(self) -> None:
        assert status_points_spent(1) == 0
        assert status_points_spent(2) == 2
        assert status_points_spent(11) == sum(status_point_cost(n) for n in range(1, 11))


class TestCompute:
    def test_stats_derivados_de_referencia(self, settings, rune_knight) -> None:
        d = engine.compute(rune_knight, settings)

        # Bônus de classe no nível 60: +1 STR, +1 AGI, +1 VIT, +1 DEX
        assert d.job_bonus_stats["str"] == 1
        assert d.total_stats["str"] == 121
        assert d.total_stats["dex"] == 91

        # HIT = nível + DEX + LUK//3 + 175
        assert d.hit == 175 + 91 + 30 // 3 + 175
        # FLEE = nível + AGI + LUK//5 + 100
        assert d.flee == 175 + 91 + 30 // 5 + 100
        # CRIT em décimos: (10 + 175//10 + 30*3) / 10
        assert d.crit == pytest.approx((10 + 17 + 90) / 10)
        # Esquiva perfeita = (LUK + 10) // 10
        assert d.perfect_dodge == 4

    def test_refino_de_arma_soma_no_lado_direito(self, settings, rune_knight) -> None:
        d = engine.compute(rune_knight, settings)
        # Fixture: arma nível 4, bônus = refino * (4+1) = 10 * 5 = 50
        assert d.weapon_atk == 220 + 50
        assert d.atk_right == d.weapon_atk + d.equip_atk

    def test_refino_soma_matk_exceto_em_arco(self, settings) -> None:
        def build(weapon_type: WeaponType) -> int:
            character = Character(
                job="Rune_Knight",
                base_level=150,
                job_level=50,
                equipment=[
                    Equipment(
                        slot=EquipSlot.RIGHT_HAND,
                        name="Arma",
                        weapon_type=weapon_type,
                        weapon_level=4,
                        base_atk=100,
                        refine=10,
                    )
                ],
            )
            return engine.compute(character, settings).weapon_matk

        assert build(WeaponType.SWORD_2H) == 50
        assert build(WeaponType.BOW) == 0

    def test_escudo_reduz_aspd(self, settings, rune_knight) -> None:
        sem_escudo = engine.compute(rune_knight, settings).aspd
        com_escudo = rune_knight.model_copy(deep=True)
        com_escudo.equipment.append(
            Equipment(slot=EquipSlot.LEFT_HAND, name="Escudo", base_def=60)
        )
        assert engine.compute(com_escudo, settings).aspd == sem_escudo - 5

    def test_aspd_respeita_o_teto(self, settings) -> None:
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
        d = engine.compute(character, settings)
        assert d.aspd <= d.aspd_cap == 193

    def test_intervalo_de_ataque_bate_com_a_formula_do_cliente(self, settings, rune_knight) -> None:
        d = engine.compute(rune_knight, settings)
        # amotion = 2000 - aspd*10; intervalo = 2 * amotion
        assert d.amotion_ms == 2000 - d.aspd * 10
        assert d.attack_interval_ms == 2 * d.amotion_ms
        assert d.attacks_per_second == pytest.approx(50 / (200 - d.aspd), rel=1e-3)

    def test_hp_cresce_com_vit(self, settings, rune_knight) -> None:
        base = engine.compute(rune_knight, settings).max_hp
        mais_vit = rune_knight.model_copy(deep=True)
        mais_vit.stats.vit += 20
        assert engine.compute(mais_vit, settings).max_hp > base

    def test_arma_nivel_5_da_patk_por_refino(self, settings) -> None:
        character = Character(
            job="Rune_Knight",
            base_level=200,
            job_level=70,
            equipment=[
                Equipment(
                    slot=EquipSlot.RIGHT_HAND,
                    name="Arma Lendária",
                    weapon_type=WeaponType.SWORD_2H,
                    weapon_level=5,
                    base_atk=300,
                    refine=10,
                )
            ],
        )
        d = engine.compute(character, settings)
        assert d.patk == 20
        assert d.smatk == 20

    def test_classe_em_portugues_resolve(self, settings, rune_knight) -> None:
        em_ptbr = rune_knight.model_copy(deep=True)
        em_ptbr.job = "Cavaleiro Rúnico"
        assert engine.compute(em_ptbr, settings).job_key == "Rune_Knight"

    def test_sem_arma_usa_punho(self, settings) -> None:
        character = Character(job="Rune_Knight", base_level=100, job_level=50)
        d = engine.compute(character, settings)
        assert d.weapon_type is WeaponType.FIST
        assert d.weapon_atk == 0


class TestJobInfo:
    def test_bonus_de_classe_e_cumulativo(self, settings) -> None:
        job = resolve_job("Rune_Knight", settings)
        assert job.cumulative_job_bonus(1)["str"] == 0
        assert job.cumulative_job_bonus(2)["str"] == 1
        assert job.cumulative_job_bonus(70)["str"] == 2

    def test_nivel_maximo_de_classe_vem_da_tabela(self, settings) -> None:
        assert resolve_job("Rune_Knight", settings).max_job_level == 70

    def test_classe_desconhecida_sugere(self, settings) -> None:
        from ragdata.errors import UnknownJob

        with pytest.raises(UnknownJob):
            resolve_job("Cavaleiro Runicoo!!", settings)
