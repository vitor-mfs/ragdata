"""Fixtures compartilhadas. Tudo roda offline."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ragdata.config import Settings, set_settings
from ragdata.gamedata import clear_job_cache
from ragdata.models import (
    BaseStats,
    Character,
    Equipment,
    EquipSlot,
    WeaponType,
)
from ragdata.ratelimit import RateLimiter

#: Tabelas reduzidas, com o mínimo para os testes serem determinísticos.
#: Os valores vêm das tabelas reais do rAthena (db/re).
JOB_STATS = {
    "Header": {"Type": "JOB_STATS", "Version": 4},
    "Body": [
        {
            "Jobs": {"Swordman": True},
            "MaxWeight": 28000,
            "HpFactor": 70,
            "SpIncrease": 200,
            "BonusStats": [
                {"Level": 2, "Str": 1},
                {"Level": 6, "Vit": 1},
                {"Level": 10, "Dex": 1},
            ],
        },
        {
            "Jobs": {"Rune_Knight": True},
            "MaxWeight": 35000,
            "HpFactor": 150,
            "SpIncrease": 300,
            "BonusStats": [
                {"Level": 2, "Str": 1},
                {"Level": 5, "Agi": 1},
                {"Level": 10, "Vit": 1},
                {"Level": 60, "Dex": 1},
                {"Level": 70, "Str": 1},
            ],
        },
    ],
}

JOB_ASPD = {
    "Header": {"Type": "JOB_ASPD", "Version": 1},
    "Body": [
        {
            "Jobs": {"Swordman": True},
            "BaseASPD": {"Fist": 40, "Dagger": 47, "1hSword": 47, "2hSword": 54, "Shield": 5},
        },
        {
            "Jobs": {"Rune_Knight": True},
            "BaseASPD": {
                "Fist": 40, "Dagger": 42, "1hSword": 42, "2hSword": 48,
                "1hSpear": 47, "2hSpear": 52, "Bow": 60, "Shield": 5,
            },
        },
    ],
}

JOB_BASEPOINTS = {
    "Header": {"Type": "JOB_BASEPOINTS", "Version": 1},
    "Body": [
        {
            "Jobs": {"Swordman": True},
            "BaseHp": [{"Level": lv, "Hp": 40 + lv * 20} for lv in range(1, 100)],
        },
        {
            "Jobs": {"Swordman": True},
            "BaseSp": [{"Level": lv, "Sp": 10 + lv * 2} for lv in range(1, 100)],
        },
        {
            "Jobs": {"Rune_Knight": True},
            "BaseHp": [{"Level": lv, "Hp": 20000 + (lv - 99) * 300} for lv in range(99, 201)],
        },
        {
            "Jobs": {"Rune_Knight": True},
            "BaseSp": [{"Level": lv, "Sp": 600 + (lv - 99) * 5} for lv in range(99, 201)],
        },
    ],
}

# Renewal: 48 pontos no nível 1, e o ganho por nível cresce a cada 5 níveis.
STATPOINT = {
    "Header": {"Type": "STATPOINT_DB", "Version": 2},
    "Body": [
        {"Level": lv, "Points": 48 + sum(3 + (n - 1) // 5 for n in range(1, lv))}
        for lv in range(1, 201)
    ],
}

REFINE = {
    "Header": {"Type": "REFINE_DB", "Version": 1},
    "Body": [
        {
            "Group": "Armor",
            "Levels": [
                {
                    "Level": 1,
                    "RefineLevels": [
                        {"Level": lv, "Bonus": lv * 100} for lv in range(1, 21)
                    ],
                }
            ],
        },
        {
            "Group": "Weapon",
            "Levels": [
                {
                    "Level": wl,
                    "RefineLevels": [
                        {"Level": lv, "Bonus": lv * (wl + 1) * 100} for lv in range(1, 21)
                    ],
                }
                for wl in range(1, 6)
            ],
        },
    ],
}

ENCHANTGRADE = {
    "Header": {"Type": "ENCHANTGRADE", "Version": 1},
    "Body": [
        {
            "Type": tipo,
            "Levels": [
                {
                    "Level": nivel,
                    "Grades": [
                        {"Grade": "None", "Bonus": 10},
                        {"Grade": "D", "Bonus": 30},
                        {"Grade": "C", "Bonus": 50},
                        {"Grade": "B", "Bonus": 100},
                    ],
                }
                for nivel in (2, 3, 4, 5)
            ],
        }
        for tipo in ("Armor", "Weapon")
    ],
}

_TABLES = {
    "job_stats": JOB_STATS,
    "job_aspd": JOB_ASPD,
    "job_basepoints": JOB_BASEPOINTS,
    "statpoint": STATPOINT,
    "refine": REFINE,
    "enchantgrade": ENCHANTGRADE,
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Configuração isolada, com as tabelas reduzidas já gravadas."""
    gamedata = tmp_path / "gamedata"
    gamedata.mkdir(parents=True)
    for name, payload in _TABLES.items():
        (gamedata / f"{name}.yml").write_text(
            yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
        )
    cfg = Settings(cache_dir=tmp_path, divine_pride_api_key="chave-de-teste")
    clear_job_cache()
    set_settings(cfg)
    yield cfg
    clear_job_cache()
    set_settings(Settings.from_env())


@pytest.fixture
def rune_knight() -> Character:
    """Um Cavaleiro Rúnico de referência, usado em vários testes."""
    return Character(
        name="Kaya",
        job="Rune_Knight",
        base_level=175,
        job_level=60,
        stats=BaseStats(**{"str": 120, "agi": 90, "vit": 80, "int": 40, "dex": 90, "luk": 30}),
        equipment=[
            Equipment(
                slot=EquipSlot.RIGHT_HAND,
                name="Espada Rúnica",
                weapon_type=WeaponType.SWORD_2H,
                weapon_level=4,
                base_atk=220,
                refine=10,
                slots=2,
            ),
            Equipment(slot=EquipSlot.ARMOR, name="Armadura", base_def=85, refine=7),
        ],
    )


@pytest.fixture
def sem_espera() -> RateLimiter:
    """Limitador de 1 req/s que avança um relógio falso em vez de dormir."""
    tempo = {"agora": 0.0}

    def sleep(segundos: float) -> None:
        tempo["agora"] += segundos

    return RateLimiter(1.0, sleep=sleep, monotonic=lambda: tempo["agora"])
