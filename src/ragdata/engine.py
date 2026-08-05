"""Motor de cálculo Renewal.

As fórmulas seguem o rAthena (`master`), que é a referência aberta do Renewal
oficial. Cada bloco cita o ponto de origem para poder ser auditado:

* `status_base_atk`          → ATK de status
* `status_base_matk_min/max` → MATK de status
* `status_calc_misc`         → HIT, FLEE, DEF2, MDEF2, traits derivados, crítico
* `status_base_amotion_pc`   → ASPD (RENEWAL_ASPD)
* `status_calc_maxhp_pc`     → HP e SP máximos
* `skill_vfcastfix`          → tempo de conjuração variável
* `pc.hpp` (`pc_leftside_*`) → como o cliente divide os números na janela

Toda divisão inteira do C é reproduzida com `//` para bater com o servidor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import MAX_ASPD_DEFAULT, MAX_ASPD_THIRD, Settings
from .gamedata import JobInfo, enchantgrade_bonus, refine_bonus, resolve_job
from .models import (
    RANGED_WEAPONS,
    Bonuses,
    Character,
    Equipment,
    WeaponType,
)

#: (DEX*2 + INT) necessário para conjuração variável instantânea.
#: `battle_config.vcast_stat_scale`, padrão 530.
VCAST_STAT_SCALE = 530

#: Valor de amotion que o cliente mostra como ASPD 0, e passo por ponto de ASPD.
AMOTION_ZERO_ASPD = 2000
AMOTION_INTERVAL = 10
#: Para jogadores, o intervalo entre ataques é o dobro do amotion.
AMOTION_DIVIDER_PC = 2

#: Classes que usam o teto de ASPD mais alto (terceiras em diante).
_THIRD_PLUS_MARKERS = (
    "Rune_Knight", "Warlock", "Ranger", "Arch_Bishop", "Mechanic", "Guillotine_Cross",
    "Royal_Guard", "Sorcerer", "Minstrel", "Wanderer", "Sura", "Genetic", "Shadow_Chaser",
    "Kagerou", "Oboro", "Rebellion", "Summoner", "Star_Emperor", "Soul_Reaper",
    "Dragon_Knight", "Meister", "Shadow_Cross", "Arch_Mage", "Cardinal", "Windhawk",
    "Imperial_Guard", "Biolo", "Abyss_Chaser", "Elemental_Master", "Inquisitor",
    "Troubadour", "Trouvere", "Sky_Emperor", "Soul_Ascetic", "Shinkiro", "Shiranui",
    "Night_Watch", "Hyper_Novice", "Spirit_Handler",
)


@dataclass
class TotalStats:
    """Atributos totais: base + bônus de nível de classe + equipamento."""

    str_: int
    agi: int
    vit: int
    int_: int
    dex: int
    luk: int
    pow: int = 0
    sta: int = 0
    wis: int = 0
    spl: int = 0
    con: int = 0
    crt: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "str": self.str_, "agi": self.agi, "vit": self.vit,
            "int": self.int_, "dex": self.dex, "luk": self.luk,
            "pow": self.pow, "sta": self.sta, "wis": self.wis,
            "spl": self.spl, "con": self.con, "crt": self.crt,
        }


@dataclass
class DerivedStats:
    """Resultado do cálculo, no mesmo formato da janela de status do jogo."""

    job_key: str
    base_level: int
    job_level: int

    base_stats: dict[str, int]
    job_bonus_stats: dict[str, int]
    equip_stats: dict[str, int]
    total_stats: dict[str, int]

    # ATK: o cliente mostra "esquerda + direita".
    status_atk: int
    weapon_atk: int
    equip_atk: int
    atk_left: int
    atk_right: int

    # MATK
    status_matk: int
    weapon_matk: int
    matk_left: int
    matk_right: int

    hit: int
    flee: int
    perfect_dodge: int
    crit: float
    soft_def: int
    hard_def: int
    soft_mdef: int
    hard_mdef: int

    aspd: int
    aspd_cap: int
    amotion_ms: int
    attack_interval_ms: int
    attacks_per_second: float

    max_hp: int
    max_sp: int

    patk: int
    smatk: int
    res: int
    mres: int
    hplus: int
    crate: int

    variable_cast_reduction: float
    """Redução de conjuração variável só por DEX/INT, em fração (0..1)."""
    variable_cast_total_reduction: float
    """Idem, já incluindo os % de redução vindos de equipamento."""
    instant_cast: bool

    weapon_type: WeaponType
    uses_dex_for_atk: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def atk_display(self) -> str:
        return f"{self.atk_left} + {self.atk_right}"

    @property
    def matk_display(self) -> str:
        return f"{self.matk_left} + {self.matk_right}"

    @property
    def def_display(self) -> str:
        return f"{self.soft_def} + {self.hard_def}"

    @property
    def mdef_display(self) -> str:
        return f"{self.soft_mdef} + {self.hard_mdef}"


def _base_job_key(job_key: str) -> str:
    """Remove os sufixos de variante: `_T` (transclasse) e `2` (montaria)."""
    return job_key.removesuffix("2").removesuffix("_T")


def _max_aspd_for(job_key: str) -> int:
    if _base_job_key(job_key) in _THIRD_PLUS_MARKERS:
        return MAX_ASPD_THIRD
    return MAX_ASPD_DEFAULT


def _is_transcendent_or_fourth(job_key: str) -> bool:
    """Classes com o multiplicador de 1.25 no HP/SP máximo.

    No rAthena é `sd->class_ & JOBL_UPPER` (transclasses) ou quarta classe.
    """
    if job_key.endswith("_T") or job_key.endswith("_High") or job_key == "Novice_High":
        return True
    trans_second = {
        "Lord_Knight", "High_Priest", "High_Wizard", "Whitesmith", "Sniper",
        "Assassin_Cross", "Paladin", "Champion", "Professor", "Stalker",
        "Creator", "Clown", "Gypsy",
    }
    fourth = {
        "Dragon_Knight", "Meister", "Shadow_Cross", "Arch_Mage", "Cardinal",
        "Windhawk", "Imperial_Guard", "Biolo", "Abyss_Chaser", "Elemental_Master",
        "Inquisitor", "Troubadour", "Trouvere", "Sky_Emperor", "Soul_Ascetic",
        "Shinkiro", "Shiranui", "Night_Watch", "Hyper_Novice", "Spirit_Handler",
    }
    return job_key in trans_second or job_key in fourth


def _refine_atk(item: Equipment, settings: Settings | None) -> int:
    """Bônus de ATK/MATK do refino de uma arma, com o extra do grau."""
    if item.weapon_level is None:
        return 0
    base = refine_bonus("Weapon", item.weapon_level, item.refine, settings)
    grade_pct = enchantgrade_bonus("Weapon", item.weapon_level, item.grade, settings)
    return base + (base * grade_pct) // 100


def _refine_def(item: Equipment, settings: Settings | None) -> int:
    """Bônus de DEF do refino de uma armadura, com o extra do grau."""
    if item.is_weapon or item.refine <= 0:
        return 0
    base = refine_bonus("Armor", 1, item.refine, settings)
    grade_pct = enchantgrade_bonus("Armor", 2, item.grade, settings)
    return base + (base * grade_pct) // 100


def total_stats(character: Character, job: JobInfo) -> TotalStats:
    """Soma atributos base, bônus de nível de classe e bônus de equipamento."""
    job_bonus = job.cumulative_job_bonus(character.job_level)
    equip = character.equipment_bonuses()
    base = character.stats
    traits = character.traits
    return TotalStats(
        str_=base.str_ + job_bonus["str"] + equip.str_,
        agi=base.agi + job_bonus["agi"] + equip.agi,
        vit=base.vit + job_bonus["vit"] + equip.vit,
        int_=base.int_ + job_bonus["int"] + equip.int_,
        dex=base.dex + job_bonus["dex"] + equip.dex,
        luk=base.luk + job_bonus["luk"] + equip.luk,
        pow=traits.pow + job_bonus["pow"] + equip.pow,
        sta=traits.sta + job_bonus["sta"] + equip.sta,
        wis=traits.wis + job_bonus["wis"] + equip.wis,
        spl=traits.spl + job_bonus["spl"] + equip.spl,
        con=traits.con + job_bonus["con"] + equip.con,
        crt=traits.crt + job_bonus["crt"] + equip.crt,
    )


def status_atk(stats: TotalStats, base_level: int, *, ranged: bool) -> int:
    """ATK de status — lado esquerdo do ATK na janela.

    rAthena `status_base_atk` (RENEWAL, BL_PC)::

        str = (dstr*10 + dex*10/5 + luk*10/3 + level*10/4) / 10 + 5 * pow

    Em armas de longa distância, DEX e STR trocam de papel.
    """
    primary = stats.dex if ranged else stats.str_
    secondary = stats.str_ if ranged else stats.dex
    value = (primary * 10 + secondary * 10 // 5 + stats.luk * 10 // 3 + base_level * 10 // 4) // 10
    return value + 5 * stats.pow


def status_matk(stats: TotalStats, base_level: int) -> int:
    """MATK de status — lado esquerdo do MATK.

    rAthena `status_base_matk_min/max` (BL_PC)::

        int + int/2 + dex/5 + luk/3 + level/4 + 5 * spl
    """
    return (
        stats.int_
        + stats.int_ // 2
        + stats.dex // 5
        + stats.luk // 3
        + base_level // 4
        + 5 * stats.spl
    )


def aspd_points(
    stats: TotalStats,
    job: JobInfo,
    *,
    weapon: WeaponType,
    ranged: bool,
    shield: bool,
    offhand: WeaponType | None,
    bonuses: Bonuses,
    cap: int,
) -> int:
    """ASPD exibido no cliente.

    rAthena `status_base_amotion_pc` (RENEWAL_ASPD)::

        temp   = sqrt(dex²/5 + agi²/2) * 0.25 + 196      (dex²/7 se longa distância)
        aspd   = int(temp + (bônus_aspd + val) * agi/200) - min(aspd_base, 200)

    Depois, em `status_calc_bl`, o bônus percentual aproxima o ASPD de 195::

        aspd += max(195 - aspd, 2) * aspd_rate2 / 100
    """
    base = job.base_aspd.get(weapon.value, 200)
    if shield:
        base += job.base_aspd.get("Shield", 0)
    elif offhand is not None:
        base += job.base_aspd.get(offhand.value, 0) // 4

    divisor = 7.0 if ranged else 5.0
    temp = math.sqrt(stats.dex * stats.dex / divisor + stats.agi * stats.agi * 0.5)
    temp = temp * 0.25 + 196
    value = int(temp + bonuses.aspd_bonus * stats.agi / 200) - min(base, 200)

    if bonuses.aspd_percent:
        value += int(max(195 - value, 2) * bonuses.aspd_percent) // 100

    return max(0, min(value, cap))


def variable_cast_reduction(stats: TotalStats) -> float:
    """Fração de redução do tempo de conjuração variável por DEX/INT.

    rAthena `skill_vfcastfix`::

        time *= 1 - sqrt((dex*2 + int) / vcast_stat_scale)

    Chega a 100% (conjuração instantânea) quando DEX*2 + INT >= 530.
    """
    ratio = (stats.dex * 2 + stats.int_) / VCAST_STAT_SCALE
    if ratio >= 1.0:
        return 1.0
    return math.sqrt(ratio)


def max_hp(stats: TotalStats, job: JobInfo, character: Character, bonuses: Bonuses) -> int:
    """HP máximo.

    rAthena `status_calc_maxhp_pc`::

        hp = base_hp[level] * (1 + vit*0.01)
        hp *= 1.25 para transclasses e quartas classes
        hp += VIT vinda de equipamento (cada ponto vale +1 de HP)
        hp += bônus planos; depois os bônus percentuais
    """
    value = float(job.hp_at(character.base_level))
    if stats.vit > 0:
        value *= 1.0 + stats.vit * 0.01
    if _is_transcendent_or_fourth(job.key):
        value *= 1.25
    value += character.equipment_bonuses().vit
    value += bonuses.max_hp_flat
    value += value * bonuses.max_hp_percent / 100.0
    return max(1, int(value))


def max_sp(stats: TotalStats, job: JobInfo, character: Character, bonuses: Bonuses) -> int:
    """SP máximo — mesma estrutura do HP, guiada por INT."""
    value = float(job.sp_at(character.base_level))
    if stats.int_ > 0:
        value *= 1.0 + stats.int_ * 0.01
    if _is_transcendent_or_fourth(job.key):
        value *= 1.25
    value += character.equipment_bonuses().int_
    value += bonuses.max_sp_flat
    value += value * bonuses.max_sp_percent / 100.0
    return max(1, int(value))


def compute(character: Character, settings: Settings | None = None) -> DerivedStats:
    """Calcula todos os stats derivados de um personagem."""
    job = resolve_job(character.job, settings)
    stats = total_stats(character, job)
    bonuses = character.equipment_bonuses()
    warnings: list[str] = []

    weapon = character.weapon_type
    ranged = weapon in RANGED_WEAPONS

    # --- ATK ---
    s_atk = status_atk(stats, character.base_level, ranged=ranged)
    weapon_atk = 0
    weapon_matk = 0
    for item in character.equipment:
        if not item.is_weapon:
            continue
        weapon_atk += item.base_atk + _refine_atk(item, settings)
        # No Renewal o refino também soma MATK, exceto em arcos.
        item_matk = item.base_matk
        if item.weapon_type is not WeaponType.BOW:
            item_matk += _refine_atk(item, settings)
        weapon_matk += item_matk

    equip_atk = bonuses.equip_atk
    atk_left = s_atk
    atk_right = weapon_atk + equip_atk
    if bonuses.atk_percent:
        atk_right = int(atk_right * (1 + bonuses.atk_percent / 100.0))

    # --- MATK ---
    s_matk = status_matk(stats, character.base_level)
    matk_right = weapon_matk + bonuses.equip_matk
    if bonuses.matk_percent:
        matk_right = int(matk_right * (1 + bonuses.matk_percent / 100.0))

    # --- Acerto, esquiva, crítico (status_calc_misc) ---
    hit = character.base_level + stats.dex + stats.luk // 3 + 175 + 2 * stats.con + bonuses.hit
    flee = character.base_level + stats.agi + stats.luk // 5 + 100 + 2 * stats.con + bonuses.flee
    perfect_dodge = (stats.luk + 10) // 10 + bonuses.perfect_dodge
    # `cri` é guardado em décimos: 10 + level/10 + luk*3.
    crit_tenths = 10 + character.base_level // 10 + stats.luk * 3 + bonuses.crit * 10
    crit = crit_tenths / 10.0

    # --- Defesa ---
    soft_def = int((character.base_level + stats.vit) / 2 + stats.agi / 5)
    soft_mdef = int(stats.int_ + character.base_level / 4 + (stats.dex + stats.vit) / 5)
    hard_def = bonuses.def_flat
    hard_mdef = bonuses.mdef_flat
    for item in character.equipment:
        if item.is_weapon:
            continue
        hard_def += item.base_def + _refine_def(item, settings)
        hard_mdef += item.base_mdef

    # --- Traits derivados ---
    patk = stats.pow // 3 + stats.con // 5 + bonuses.patk
    smatk = stats.spl // 3 + stats.con // 5 + bonuses.smatk
    res = stats.sta + stats.sta // 3 * 5 + bonuses.res
    mres = stats.wis + stats.wis // 3 * 5 + bonuses.mres
    hplus = stats.crt + bonuses.hplus
    crate = stats.crt // 3 + bonuses.crate
    # Armas de nível 5 dão PAtk/SMatk pelo refino.
    for item in character.equipment:
        if item.is_weapon and item.weapon_level == 5:
            patk += item.refine * 2
            smatk += item.refine * 2

    # --- ASPD ---
    cap = _max_aspd_for(job.key)
    offhand = character.left_hand.weapon_type if character.is_dual_wielding else None
    aspd = aspd_points(
        stats,
        job,
        weapon=weapon,
        ranged=ranged,
        shield=character.has_shield,
        offhand=offhand,
        bonuses=bonuses,
        cap=cap,
    )
    amotion = AMOTION_ZERO_ASPD - aspd * AMOTION_INTERVAL
    attack_interval = AMOTION_DIVIDER_PC * amotion
    aps = 1000.0 / attack_interval if attack_interval > 0 else 0.0

    # --- Conjuração ---
    vcast_stat = variable_cast_reduction(stats)
    remaining = (1 - vcast_stat) * max(0.0, 1 - min(bonuses.variable_cast_percent, 100) / 100.0)
    vcast_total = 1 - remaining

    # --- Avisos honestos sobre o que o motor não modela ---
    if any(item.grade > 0 for item in character.equipment):
        warnings.append(
            "Itens com grau de encantamento: o bônus de refino já considera o grau, "
            "mas efeitos adicionais do grau (se houver) precisam entrar como bônus manuais."
        )
    if any(card.note for item in character.equipment for card in item.cards):
        warnings.append(
            "Há cartas com efeitos descritos em texto (condicionais, por raça/elemento). "
            "Elas não entram nos números — considere-as ao ler as sugestões."
        )
    if character.is_dual_wielding and weapon in RANGED_WEAPONS:
        warnings.append("Arma de longa distância com item na mão esquerda: confira a leitura do print.")

    return DerivedStats(
        job_key=job.key,
        base_level=character.base_level,
        job_level=character.job_level,
        base_stats=character.stats.as_dict() | character.traits.as_dict(),
        job_bonus_stats=job.cumulative_job_bonus(character.job_level),
        equip_stats=bonuses.stat_bonuses() | bonuses.trait_bonuses(),
        total_stats=stats.as_dict(),
        status_atk=s_atk,
        weapon_atk=weapon_atk,
        equip_atk=equip_atk,
        atk_left=atk_left,
        atk_right=atk_right,
        status_matk=s_matk,
        weapon_matk=weapon_matk,
        matk_left=s_matk,
        matk_right=matk_right,
        hit=hit,
        flee=flee,
        perfect_dodge=perfect_dodge,
        crit=crit,
        soft_def=soft_def,
        hard_def=hard_def,
        soft_mdef=soft_mdef,
        hard_mdef=hard_mdef,
        aspd=aspd,
        aspd_cap=cap,
        amotion_ms=amotion,
        attack_interval_ms=attack_interval,
        attacks_per_second=round(aps, 3),
        max_hp=max_hp(stats, job, character, bonuses),
        max_sp=max_sp(stats, job, character, bonuses),
        patk=patk,
        smatk=smatk,
        res=res,
        mres=mres,
        hplus=hplus,
        crate=crate,
        variable_cast_reduction=round(vcast_stat, 4),
        variable_cast_total_reduction=round(vcast_total, 4),
        instant_cast=vcast_total >= 1.0,
        weapon_type=weapon,
        uses_dex_for_atk=ranged,
        warnings=warnings,
    )
