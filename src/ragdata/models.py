"""Modelos de domínio: personagem, equipamentos, stats.

Convenção: identificadores em inglês (alinhados ao rAthena, que é a fonte das
fórmulas), textos voltados ao usuário em português.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WeaponType(StrEnum):
    """Tipos de arma, com os mesmos nomes usados em `db/re/job_aspd.yml`."""

    FIST = "Fist"
    DAGGER = "Dagger"
    SWORD_1H = "1hSword"
    SWORD_2H = "2hSword"
    SPEAR_1H = "1hSpear"
    SPEAR_2H = "2hSpear"
    AXE_1H = "1hAxe"
    AXE_2H = "2hAxe"
    MACE = "Mace"
    MACE_2H = "2hMace"
    STAFF = "Staff"
    STAFF_2H = "2hStaff"
    BOW = "Bow"
    KNUCKLE = "Knuckle"
    MUSICAL = "Musical"
    WHIP = "Whip"
    BOOK = "Book"
    KATAR = "Katar"
    REVOLVER = "Revolver"
    RIFLE = "Rifle"
    GATLING = "Gatling"
    SHOTGUN = "Shotgun"
    GRENADE = "Grenade"
    HUUMA = "Huuma"


#: Armas que usam DEX como stat primário de ATK e a curva de ASPD de longo alcance.
#: Espelha o `switch` em `status_base_atk` / `status_base_amotion_pc` do rAthena.
RANGED_WEAPONS: frozenset[WeaponType] = frozenset(
    {
        WeaponType.BOW,
        WeaponType.MUSICAL,
        WeaponType.WHIP,
        WeaponType.REVOLVER,
        WeaponType.RIFLE,
        WeaponType.GATLING,
        WeaponType.SHOTGUN,
        WeaponType.GRENADE,
    }
)

#: Armas de duas mãos — não aceitam escudo nem segunda arma.
TWO_HANDED_WEAPONS: frozenset[WeaponType] = frozenset(
    {
        WeaponType.SWORD_2H,
        WeaponType.SPEAR_2H,
        WeaponType.AXE_2H,
        WeaponType.MACE_2H,
        WeaponType.STAFF_2H,
        WeaponType.BOW,
        WeaponType.KATAR,
        WeaponType.HUUMA,
        WeaponType.RIFLE,
        WeaponType.GATLING,
        WeaponType.SHOTGUN,
        WeaponType.GRENADE,
    }
)


class EquipSlot(StrEnum):
    """Posições de equipamento da janela de equipamentos."""

    HEAD_TOP = "head_top"
    HEAD_MID = "head_mid"
    HEAD_LOW = "head_low"
    ARMOR = "armor"
    RIGHT_HAND = "right_hand"
    LEFT_HAND = "left_hand"
    GARMENT = "garment"
    SHOES = "shoes"
    ACCESSORY_1 = "accessory_1"
    ACCESSORY_2 = "accessory_2"
    SHADOW_ARMOR = "shadow_armor"
    SHADOW_WEAPON = "shadow_weapon"
    SHADOW_SHIELD = "shadow_shield"
    SHADOW_SHOES = "shadow_shoes"
    SHADOW_ACCESSORY_L = "shadow_accessory_l"
    SHADOW_ACCESSORY_R = "shadow_accessory_r"


#: Slots que contam como "arma" para efeito de ASPD/ATK.
WEAPON_SLOTS = (EquipSlot.RIGHT_HAND, EquipSlot.LEFT_HAND)


class Race(StrEnum):
    FORMLESS = "formless"
    UNDEAD = "undead"
    BRUTE = "brute"
    PLANT = "plant"
    INSECT = "insect"
    FISH = "fish"
    DEMON = "demon"
    DEMIHUMAN = "demihuman"
    ANGEL = "angel"
    DRAGON = "dragon"
    PLAYER = "player"


class Element(StrEnum):
    NEUTRAL = "neutral"
    WATER = "water"
    EARTH = "earth"
    FIRE = "fire"
    WIND = "wind"
    POISON = "poison"
    HOLY = "holy"
    SHADOW = "shadow"
    GHOST = "ghost"
    UNDEAD = "undead"


class Size(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class Goal(StrEnum):
    """Objetivo declarado pelo jogador — orienta as sugestões."""

    LEVELING = "leveling"
    FARM = "farm"
    MVP = "mvp"
    PVP = "pvp"
    WOE = "woe"
    TANK = "tank"
    SUPPORT = "support"


class BaseStats(BaseModel):
    """Os seis atributos clássicos (valor total exibido na janela de status)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    str_: Annotated[int, Field(ge=1, alias="str")] = 1
    agi: Annotated[int, Field(ge=1)] = 1
    vit: Annotated[int, Field(ge=1)] = 1
    int_: Annotated[int, Field(ge=1, alias="int")] = 1
    dex: Annotated[int, Field(ge=1)] = 1
    luk: Annotated[int, Field(ge=1)] = 1

    def as_dict(self) -> dict[str, int]:
        return {
            "str": self.str_,
            "agi": self.agi,
            "vit": self.vit,
            "int": self.int_,
            "dex": self.dex,
            "luk": self.luk,
        }


class TraitStats(BaseModel):
    """Atributos de trait (4ª classe). Zerados para classes anteriores."""

    model_config = ConfigDict(extra="forbid")

    pow: Annotated[int, Field(ge=0)] = 0
    sta: Annotated[int, Field(ge=0)] = 0
    wis: Annotated[int, Field(ge=0)] = 0
    spl: Annotated[int, Field(ge=0)] = 0
    con: Annotated[int, Field(ge=0)] = 0
    crt: Annotated[int, Field(ge=0)] = 0

    def total(self) -> int:
        return self.pow + self.sta + self.wis + self.spl + self.con + self.crt

    def as_dict(self) -> dict[str, int]:
        return self.model_dump()


class Bonuses(BaseModel):
    """Bônus agregados vindos de equipamentos, cartas e encantamentos.

    O ragdata **não** interpreta scripts de item. Estes campos são preenchidos
    a partir da descrição do item (Divine Pride) ou informados por quem monta a
    build. Tudo aqui é somado ao resultado do cálculo base.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Atributos concedidos por equipamento (contam para as fórmulas de status).
    str_: Annotated[int, Field(alias="str")] = 0
    agi: int = 0
    vit: int = 0
    int_: Annotated[int, Field(alias="int")] = 0
    dex: int = 0
    luk: int = 0
    pow: int = 0
    sta: int = 0
    wis: int = 0
    spl: int = 0
    con: int = 0
    crt: int = 0

    # Ataque / magia
    equip_atk: int = 0
    """Somado ao lado direito do ATK (bonus.eatk no rAthena)."""
    equip_matk: int = 0
    """Somado ao lado direito do MATK (bonus.ematk no rAthena)."""
    atk_percent: float = 0.0
    matk_percent: float = 0.0

    # Defensivo
    def_flat: int = 0
    """DEF vinda de equipamento (lado direito do DEF)."""
    mdef_flat: int = 0
    max_hp_flat: int = 0
    max_hp_percent: float = 0.0
    max_sp_flat: int = 0
    max_sp_percent: float = 0.0

    # Acerto / esquiva / crítico
    hit: int = 0
    flee: int = 0
    perfect_dodge: int = 0
    crit: int = 0
    """Crítico em pontos inteiros exibidos (ex.: +7 de Crit)."""
    crit_damage_percent: float = 0.0

    # Velocidade e conjuração
    aspd_percent: float = 0.0
    """Bônus percentual de ASPD (aproxima o ASPD de 195, como no Renewal)."""
    aspd_bonus: float = 0.0
    """Bônus plano de ASPD, escalado por AGI/200 (ex.: Buster/Agi Up)."""
    variable_cast_percent: float = 0.0
    """Redução de tempo de conjuração variável, em % (ex.: 30 = -30%)."""
    fixed_cast_percent: float = 0.0
    fixed_cast_flat_ms: int = 0

    # Trait derivados concedidos diretamente por equipamento
    patk: int = 0
    smatk: int = 0
    res: int = 0
    mres: int = 0
    hplus: int = 0
    crate: int = 0

    def __add__(self, other: Bonuses) -> Bonuses:
        if not isinstance(other, Bonuses):  # pragma: no cover - defensivo
            return NotImplemented
        merged: dict[str, float | int] = {}
        for name in type(self).model_fields:
            merged[name] = getattr(self, name) + getattr(other, name)
        return Bonuses.model_validate(merged)

    @classmethod
    def sum(cls, items: list[Bonuses]) -> Bonuses:
        total = cls()
        for item in items:
            total = total + item
        return total

    def stat_bonuses(self) -> dict[str, int]:
        return {
            "str": self.str_,
            "agi": self.agi,
            "vit": self.vit,
            "int": self.int_,
            "dex": self.dex,
            "luk": self.luk,
        }

    def trait_bonuses(self) -> dict[str, int]:
        return {
            "pow": self.pow,
            "sta": self.sta,
            "wis": self.wis,
            "spl": self.spl,
            "con": self.con,
            "crt": self.crt,
        }


class Card(BaseModel):
    """Carta ou encantamento em um slot."""

    model_config = ConfigDict(extra="forbid")

    name: str
    item_id: int | None = None
    bonuses: Bonuses = Field(default_factory=Bonuses)
    note: str | None = None
    """Efeito que não cabe nos campos numéricos (ex.: '+30% dano em Demônio')."""


class Equipment(BaseModel):
    """Uma peça equipada."""

    model_config = ConfigDict(extra="forbid")

    slot: EquipSlot
    name: str
    item_id: int | None = None
    refine: Annotated[int, Field(ge=0, le=20)] = 0
    grade: Annotated[int, Field(ge=0, le=4)] = 0
    """Grau de encantamento (0 = nenhum, 1..4 = D..A)."""

    # Dados que normalmente vêm do Divine Pride
    weapon_type: WeaponType | None = None
    weapon_level: Annotated[int, Field(ge=1, le=5)] | None = None
    base_atk: int = 0
    base_matk: int = 0
    base_def: int = 0
    base_mdef: int = 0
    slots: Annotated[int, Field(ge=0, le=4)] = 0

    cards: list[Card] = Field(default_factory=list)
    bonuses: Bonuses = Field(default_factory=Bonuses)
    note: str | None = None

    @model_validator(mode="after")
    def _check_cards_fit(self) -> Equipment:
        if self.slots and len(self.cards) > self.slots:
            raise ValueError(
                f"{self.name}: {len(self.cards)} cartas em um item de {self.slots} slot(s)"
            )
        return self

    @property
    def is_weapon(self) -> bool:
        return self.weapon_type is not None

    def total_bonuses(self) -> Bonuses:
        return Bonuses.sum([self.bonuses, *(c.bonuses for c in self.cards)])

    def free_slots(self) -> int:
        return max(0, self.slots - len(self.cards))


class SkillEntry(BaseModel):
    """Uma habilidade e seu nível."""

    model_config = ConfigDict(extra="forbid")

    name: str
    level: Annotated[int, Field(ge=1, le=20)]
    skill_id: int | None = None


class Character(BaseModel):
    """Um personagem completo, pronto para análise."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    job: str
    """Classe. Aceita nome em PT-BR ou a chave do rAthena (ex.: 'Rune_Knight')."""
    base_level: Annotated[int, Field(ge=1, le=300)] = 1
    job_level: Annotated[int, Field(ge=1, le=100)] = 1

    stats: BaseStats = Field(default_factory=BaseStats)
    """Atributos **base** (o valor à esquerda, sem os bônus de equipamento)."""
    traits: TraitStats = Field(default_factory=TraitStats)

    equipment: list[Equipment] = Field(default_factory=list)
    skills: list[SkillEntry] = Field(default_factory=list)

    goal: Goal | None = None
    notes: str | None = None

    @field_validator("equipment")
    @classmethod
    def _one_item_per_slot(cls, items: list[Equipment]) -> list[Equipment]:
        seen: set[EquipSlot] = set()
        for item in items:
            if item.slot in seen:
                raise ValueError(f"Mais de um item na posição {item.slot.value}")
            seen.add(item.slot)
        return items

    def item_in(self, slot: EquipSlot) -> Equipment | None:
        for item in self.equipment:
            if item.slot == slot:
                return item
        return None

    @property
    def right_hand(self) -> Equipment | None:
        return self.item_in(EquipSlot.RIGHT_HAND)

    @property
    def left_hand(self) -> Equipment | None:
        return self.item_in(EquipSlot.LEFT_HAND)

    @property
    def weapon_type(self) -> WeaponType:
        rh = self.right_hand
        if rh is not None and rh.weapon_type is not None:
            return rh.weapon_type
        return WeaponType.FIST

    @property
    def has_shield(self) -> bool:
        lh = self.left_hand
        return lh is not None and lh.weapon_type is None

    @property
    def is_dual_wielding(self) -> bool:
        lh = self.left_hand
        return lh is not None and lh.weapon_type is not None

    def equipment_bonuses(self) -> Bonuses:
        return Bonuses.sum([e.total_bonuses() for e in self.equipment])

    def skill_level(self, name: str) -> int:
        key = name.strip().casefold()
        for skill in self.skills:
            if skill.name.strip().casefold() == key:
                return skill.level
        return 0


class TargetMonster(BaseModel):
    """Alvo de referência (dados do Divine Pride) para orientar sugestões."""

    model_config = ConfigDict(extra="forbid")

    name: str
    monster_id: int | None = None
    level: int | None = None
    hp: int | None = None
    race: Race | None = None
    element: Element | None = None
    element_level: Annotated[int, Field(ge=1, le=4)] | None = None
    size: Size | None = None
    defense: int | None = None
    magic_defense: int | None = None
    flee: int | None = None
    hit: int | None = None
    is_mvp: bool = False


Severity = Literal["critico", "aviso", "dica"]


class Finding(BaseModel):
    """Um apontamento da análise."""

    model_config = ConfigDict(extra="forbid")

    severity: Severity
    category: str
    title: str
    detail: str
    gain: str | None = None
    """Ganho estimado, quando calculável (ex.: '+7 ASPD', '-12 pontos gastos')."""
