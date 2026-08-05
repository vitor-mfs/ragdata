"""Tabelas de jogo do rAthena (Renewal), baixadas sob demanda.

Por que rAthena: é a implementação aberta de referência das fórmulas Renewal, e
as tabelas (ASPD base por classe/arma, HP/SP por nível, pontos de atributo,
bônus de refino) são exatamente o que o motor precisa.

Por que **não** versionamos esses arquivos: o rAthena é GPL-3.0. Baixamos para o
cache local do usuário em vez de embutir no repositório. `ragdata setup` faz o
download; o motor levanta `GameDataMissing` com a instrução se faltar.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import RATHENA_TABLES, Settings, get_settings
from .errors import GameDataMissing, UnknownJob

#: Ordem dos atributos como aparecem nas tabelas do rAthena.
STAT_KEYS = ("Str", "Agi", "Vit", "Int", "Dex", "Luk")
TRAIT_KEYS = ("Pow", "Sta", "Wis", "Spl", "Con", "Crt")


def table_path(name: str, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.gamedata_dir / f"{name}.yml"


def load_table(name: str, settings: Settings | None = None) -> dict[str, Any]:
    """Carrega uma tabela YAML já baixada."""
    if name not in RATHENA_TABLES:
        raise KeyError(f"Tabela desconhecida: {name}")
    path = table_path(name, settings)
    if not path.exists():
        raise GameDataMissing(name)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "Body" not in data:
        raise GameDataMissing(f"{name} (arquivo inválido em {path})")
    return data


@dataclass
class JobInfo:
    """Dados de uma classe, consolidados a partir das tabelas do rAthena.

    `base_hp`/`base_sp` são mapas nível→valor, e não listas: as terceiras e
    quartas classes só têm entradas a partir do nível 99.
    """

    key: str
    base_aspd: dict[str, int] = field(default_factory=dict)
    bonus_stats: list[dict[str, Any]] = field(default_factory=list)
    base_hp: dict[int, int] = field(default_factory=dict)
    base_sp: dict[int, int] = field(default_factory=dict)
    hp_factor: int = 0
    hp_increase: int = 500
    sp_factor: int = 0
    sp_increase: int = 100

    @property
    def max_base_level(self) -> int:
        """Maior nível base tabelado, ou 0 quando a classe não tem tabela.

        Sem tabela o HP vem da fórmula (`calc_basehp`) e não dá para inferir o
        teto — os chamadores devem tratar 0 como "desconhecido".
        """
        return max(self.base_hp) if self.base_hp else 0

    @property
    def min_base_level(self) -> int:
        """Menor nível base tabelado — 99 para terceiras classes."""
        return min(self.base_hp) if self.base_hp else 1

    @property
    def max_job_level(self) -> int:
        """Inferido do último nível com bônus de classe na tabela."""
        levels = [int(e.get("Level", 0)) for e in self.bonus_stats]
        return max(levels) if levels else 50

    def cumulative_job_bonus(self, job_level: int) -> dict[str, int]:
        """Bônus de atributo acumulado até `job_level` (inclusive).

        No Renewal a classe concede atributos conforme o nível de classe sobe;
        esses pontos são somados aos atributos base e **não** custam pontos.
        """
        totals = {k.lower(): 0 for k in (*STAT_KEYS, *TRAIT_KEYS)}
        for entry in self.bonus_stats:
            if int(entry.get("Level", 0)) > job_level:
                continue
            for key in (*STAT_KEYS, *TRAIT_KEYS):
                if key in entry:
                    totals[key.lower()] += int(entry[key])
        return totals

    def hp_at(self, base_level: int) -> int:
        """HP base no nível, pela tabela ou pela fórmula linear/exponencial.

        rAthena `JobDatabase::calc_basehp` — usada quando a classe não tem
        tabela própria (é o caso das quartas classes em algumas revisões).
        """
        tabled = self.base_hp.get(base_level)
        if tabled is not None:
            return tabled
        value = 35.0 + (base_level * (self.hp_increase / 100.0)) // 1
        for i in range(2, base_level + 1):
            value += ((self.hp_factor / 100.0) * i + 0.5) // 1
        if self.key.startswith("Summoner") or self.key == "Spirit_Handler":
            value += (value / 2 + 0.5) // 1
        elif "Super_Novice" in self.key or self.key == "Hyper_Novice":
            if base_level >= 99:
                value += 2000
            if base_level >= 150:
                value += 2000
        return int(value)

    def sp_at(self, base_level: int) -> int:
        """SP base no nível. rAthena `JobDatabase::calc_basesp`."""
        tabled = self.base_sp.get(base_level)
        if tabled is not None:
            return tabled
        value = 10.0 + (base_level * (self.sp_increase / 100.0)) // 1
        for i in range(2, base_level + 1):
            value += ((self.sp_factor / 100.0) * i + 0.5) // 1
        if self.key.startswith("Ninja") or self.key in {"Kagerou", "Oboro", "Shinkiro", "Shiranui"}:
            value = value - 22 if base_level >= 10 else 11 + 3 * base_level
        elif self.key in {"Gunslinger", "Rebellion", "Night_Watch"}:
            value = value - 18 if base_level >= 10 else 9 + 3 * base_level
        elif self.key.startswith("Summoner") or self.key == "Spirit_Handler":
            value += (value / 2 + 0.5) // 1
        return int(value)


def _jobs_of(entry: dict[str, Any]) -> list[str]:
    jobs = entry.get("Jobs") or {}
    return [name for name, enabled in jobs.items() if enabled]


@functools.lru_cache(maxsize=1)
def _job_index(gamedata_dir: str) -> dict[str, JobInfo]:
    """Índice classe→JobInfo. Cacheado por diretório de dados."""
    settings = Settings(cache_dir=Path(gamedata_dir).parent)
    index: dict[str, JobInfo] = {}

    def info_for(job: str) -> JobInfo:
        return index.setdefault(job, JobInfo(key=job))

    # Uma classe pode aparecer em mais de uma entrada (uma traz BaseHp, outra
    # BaseSp, etc.), então cada campo só é escrito quando está presente.
    for entry in load_table("job_stats", settings)["Body"]:
        for job in _jobs_of(entry):
            info = info_for(job)
            if entry.get("BonusStats"):
                info.bonus_stats = entry["BonusStats"]
            if "HpFactor" in entry:
                info.hp_factor = int(entry["HpFactor"])
            if "HpIncrease" in entry:
                info.hp_increase = int(entry["HpIncrease"])
            if "SpIncrease" in entry:
                info.sp_increase = int(entry["SpIncrease"])
            if "SpFactor" in entry:
                info.sp_factor = int(entry["SpFactor"])

    for entry in load_table("job_aspd", settings)["Body"]:
        aspd = {k: int(v) for k, v in (entry.get("BaseASPD") or {}).items()}
        if not aspd:
            continue
        for job in _jobs_of(entry):
            info_for(job).base_aspd = aspd

    for entry in load_table("job_basepoints", settings)["Body"]:
        hp = {int(row["Level"]): int(row["Hp"]) for row in (entry.get("BaseHp") or [])}
        sp = {int(row["Level"]): int(row["Sp"]) for row in (entry.get("BaseSp") or [])}
        for job in _jobs_of(entry):
            info = info_for(job)
            if hp:
                info.base_hp.update(hp)
            if sp:
                info.base_sp.update(sp)

    return index


def job_index(settings: Settings | None = None) -> dict[str, JobInfo]:
    settings = settings or get_settings()
    return _job_index(str(settings.gamedata_dir))


def clear_job_cache() -> None:
    """Esquece o índice em memória (útil após `ragdata setup` ou em testes)."""
    _job_index.cache_clear()


@functools.lru_cache(maxsize=1)
def _statpoint_table(gamedata_dir: str) -> dict[int, int]:
    settings = Settings(cache_dir=Path(gamedata_dir).parent)
    data = load_table("statpoint", settings)
    return {int(row["Level"]): int(row["Points"]) for row in data["Body"]}


def status_points_at(base_level: int, settings: Settings | None = None) -> int:
    """Total de pontos de atributo disponíveis naquele nível base.

    A tabela `statpoint.yml` do rAthena guarda o total acumulado por nível.
    """
    settings = settings or get_settings()
    table = _statpoint_table(str(settings.gamedata_dir))
    if not table:
        raise GameDataMissing("statpoint")
    if base_level in table:
        return table[base_level]
    # Acima do último nível tabelado, mantém o último valor conhecido.
    return table[max(table)]


def status_point_cost(current: int) -> int:
    """Custo em pontos para subir um atributo de `current` para `current + 1`.

    Fórmula Renewal (`PC_STATUS_POINT_COST` em `src/map/pc.cpp`):
    `2 + (n-1)/10` abaixo de 100; `16 + 4*((n-100)/5)` de 100 em diante.
    """
    if current < 100:
        return 2 + (current - 1) // 10
    return 16 + 4 * ((current - 100) // 5)


def status_points_spent(value: int) -> int:
    """Pontos gastos para levar um atributo de 1 até `value`."""
    return sum(status_point_cost(n) for n in range(1, value))


@functools.lru_cache(maxsize=1)
def _refine_table(gamedata_dir: str) -> dict[tuple[str, int], dict[int, int]]:
    """(grupo, nível_do_item) → {refino: bônus}.

    `Bonus` no `refine.yml` é multiplicado por 100 (o rAthena divide por 100 ao
    aplicar), então normalizamos aqui.
    """
    settings = Settings(cache_dir=Path(gamedata_dir).parent)
    data = load_table("refine", settings)
    out: dict[tuple[str, int], dict[int, int]] = {}
    for entry in data["Body"]:
        group = str(entry.get("Group", ""))
        for level_entry in entry.get("Levels") or []:
            item_level = int(level_entry.get("Level", 1))
            bonuses: dict[int, int] = {}
            for refine_entry in level_entry.get("RefineLevels") or []:
                bonuses[int(refine_entry["Level"])] = int(refine_entry.get("Bonus", 0)) // 100
            out[(group, item_level)] = bonuses
    return out


def refine_bonus(group: str, item_level: int, refine: int, settings: Settings | None = None) -> int:
    """Bônus de refino (ATK para armas, DEF para armaduras).

    `group` é "Weapon", "Armor", "Shadow_Armor" etc., como em `refine.yml`.
    """
    if refine <= 0:
        return 0
    settings = settings or get_settings()
    table = _refine_table(str(settings.gamedata_dir))
    bonuses = table.get((group, item_level))
    if bonuses is None:
        # Armaduras usam Level 1 em todas as entradas do refine.yml.
        bonuses = table.get((group, 1))
    if not bonuses:
        raise GameDataMissing(f"refine ({group} nível {item_level})")
    best = 0
    for level, bonus in sorted(bonuses.items()):
        if level <= refine:
            best = bonus
    return best


#: Nomes de grau usados no `enchantgrade.yml`, na ordem dos índices 0..4.
GRADE_NAMES = ("None", "D", "C", "B", "A")


@functools.lru_cache(maxsize=1)
def _enchantgrade_table(gamedata_dir: str) -> dict[tuple[str, int, str], int]:
    """(tipo, nível_do_item, grau) → bônus percentual sobre o refino."""
    settings = Settings(cache_dir=Path(gamedata_dir).parent)
    data = load_table("enchantgrade", settings)
    out: dict[tuple[str, int, str], int] = {}
    for entry in data["Body"]:
        item_type = str(entry.get("Type", ""))
        for level_entry in entry.get("Levels") or []:
            item_level = int(level_entry.get("Level", 1))
            for grade_entry in level_entry.get("Grades") or []:
                grade = str(grade_entry.get("Grade", "None"))
                out[(item_type, item_level, grade)] = int(grade_entry.get("Bonus", 0))
    return out


def enchantgrade_bonus(
    item_type: str, item_level: int, grade: int, settings: Settings | None = None
) -> int:
    """Percentual extra aplicado sobre o bônus de refino, pelo grau.

    `grade` é 0 (nenhum) a 4 (A). Retorna 0 quando não há entrada — grau só
    existe para equipamentos modernos de nível alto.
    """
    if grade <= 0:
        return 0
    settings = settings or get_settings()
    table = _enchantgrade_table(str(settings.gamedata_dir))
    name = GRADE_NAMES[min(grade, len(GRADE_NAMES) - 1)]
    return table.get((item_type, item_level, name), 0)


def resolve_job(name: str, settings: Settings | None = None) -> JobInfo:
    """Resolve um nome de classe (PT-BR, inglês ou chave rAthena) para JobInfo."""
    from .jobs import canonical_job_key, suggest_jobs

    index = job_index(settings)
    key = canonical_job_key(name, known=set(index))
    if key is None:
        raise UnknownJob(name, suggest_jobs(name, known=set(index)))
    return index[key]
