"""Análise de build: contabilidade de pontos, breakpoints e sugestões.

Três camadas, do mais objetivo ao mais opinativo:

1. `stat_point_audit` — aritmética pura: quantos pontos a build usa e quantos o
   nível concede. Serve também para pegar erro de leitura de print.
2. `find_breakpoints` — o próximo ponto em que gastar atributo muda um número
   inteiro (ASPD, conjuração instantânea, teto de atributo).
3. `suggest` — recomendações por objetivo, sempre acompanhadas do número que
   as sustenta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .engine import DerivedStats, aspd_points, compute, total_stats
from .gamedata import (
    JobInfo,
    resolve_job,
    status_point_cost,
    status_points_at,
    status_points_spent,
)
from .models import (
    RANGED_WEAPONS,
    TWO_HANDED_WEAPONS,
    Character,
    EquipSlot,
    Finding,
    Goal,
    TargetMonster,
)

#: Teto de atributo por família de classe (conf/battle/player.conf do rAthena).
STAT_CAP_DEFAULT = 99
STAT_CAP_THIRD_PLUS = 130
TRAIT_CAP = 110

#: Classes com teto de atributo 130.
_HIGH_CAP_JOBS = frozenset(
    {
        "Rune_Knight", "Warlock", "Ranger", "Arch_Bishop", "Mechanic", "Guillotine_Cross",
        "Royal_Guard", "Sorcerer", "Minstrel", "Wanderer", "Sura", "Genetic", "Shadow_Chaser",
        "Kagerou", "Oboro", "Rebellion", "Summoner", "Star_Emperor", "Soul_Reaper",
        "Dragon_Knight", "Meister", "Shadow_Cross", "Arch_Mage", "Cardinal", "Windhawk",
        "Imperial_Guard", "Biolo", "Abyss_Chaser", "Elemental_Master", "Inquisitor",
        "Troubadour", "Trouvere", "Sky_Emperor", "Soul_Ascetic", "Shinkiro", "Shiranui",
        "Night_Watch", "Hyper_Novice", "Spirit_Handler",
    }
)

#: Slots de equipamento que uma build "completa" costuma preencher.
_CORE_SLOTS = (
    EquipSlot.HEAD_TOP,
    EquipSlot.ARMOR,
    EquipSlot.RIGHT_HAND,
    EquipSlot.GARMENT,
    EquipSlot.SHOES,
    EquipSlot.ACCESSORY_1,
    EquipSlot.ACCESSORY_2,
)

_SLOT_LABELS = {
    EquipSlot.HEAD_TOP: "topo da cabeça",
    EquipSlot.HEAD_MID: "meio da cabeça",
    EquipSlot.HEAD_LOW: "baixo da cabeça",
    EquipSlot.ARMOR: "armadura",
    EquipSlot.RIGHT_HAND: "mão direita",
    EquipSlot.LEFT_HAND: "mão esquerda",
    EquipSlot.GARMENT: "capa",
    EquipSlot.SHOES: "calçado",
    EquipSlot.ACCESSORY_1: "acessório 1",
    EquipSlot.ACCESSORY_2: "acessório 2",
}

_STAT_LABELS = {
    "str": "STR", "agi": "AGI", "vit": "VIT",
    "int": "INT", "dex": "DEX", "luk": "LUK",
}


def stat_cap(job_key: str) -> int:
    """Teto de atributo base da classe."""
    from .engine import _base_job_key

    return STAT_CAP_THIRD_PLUS if _base_job_key(job_key) in _HIGH_CAP_JOBS else STAT_CAP_DEFAULT


@dataclass
class StatPointAudit:
    """Contabilidade dos pontos de atributo."""

    available: int
    spent: int
    per_stat: dict[str, int]
    consistent: bool

    @property
    def remaining(self) -> int:
        return self.available - self.spent


@dataclass
class Breakpoint:
    """Um limiar alcançável e o que ele custa."""

    stat: str
    from_value: int
    to_value: int
    cost_points: int
    effect: str


@dataclass
class BuildReport:
    """Resultado completo da análise."""

    character: Character
    derived: DerivedStats
    audit: StatPointAudit
    breakpoints: list[Breakpoint] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    target: TargetMonster | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "personagem": {
                "nome": self.character.name,
                "classe": self.derived.job_key,
                "nivel_base": self.character.base_level,
                "nivel_classe": self.character.job_level,
                "objetivo": self.character.goal.value if self.character.goal else None,
            },
            "stats": {
                "base": self.derived.base_stats,
                "bonus_de_classe": self.derived.job_bonus_stats,
                "bonus_de_equipamento": self.derived.equip_stats,
                "total": self.derived.total_stats,
            },
            "derivados": {
                "atk": self.derived.atk_display,
                "matk": self.derived.matk_display,
                "hit": self.derived.hit,
                "flee": self.derived.flee,
                "crit": self.derived.crit,
                "esquiva_perfeita": self.derived.perfect_dodge,
                "def": self.derived.def_display,
                "mdef": self.derived.mdef_display,
                "aspd": self.derived.aspd,
                "aspd_teto": self.derived.aspd_cap,
                "ataques_por_segundo": self.derived.attacks_per_second,
                "hp_max": self.derived.max_hp,
                "sp_max": self.derived.max_sp,
                "patk": self.derived.patk,
                "smatk": self.derived.smatk,
                "res": self.derived.res,
                "mres": self.derived.mres,
                "reducao_cast_variavel": self.derived.variable_cast_total_reduction,
                "cast_instantaneo": self.derived.instant_cast,
            },
            "pontos": {
                "disponiveis": self.audit.available,
                "gastos": self.audit.spent,
                "sobrando": self.audit.remaining,
                "consistente": self.audit.consistent,
                "por_atributo": self.audit.per_stat,
            },
            "breakpoints": [
                {
                    "atributo": bp.stat,
                    "de": bp.from_value,
                    "para": bp.to_value,
                    "custo_em_pontos": bp.cost_points,
                    "efeito": bp.effect,
                }
                for bp in self.breakpoints
            ],
            "achados": [f.model_dump() for f in self.findings],
            "alvo": self.target.model_dump() if self.target else None,
            "avisos_do_motor": self.derived.warnings,
        }


# ---------------------------------------------------------------- auditoria


def stat_point_audit(character: Character, settings: Settings | None = None) -> StatPointAudit:
    """Compara os pontos gastos nos atributos base com os que o nível concede."""
    per_stat = {
        name: status_points_spent(value)
        for name, value in character.stats.as_dict().items()
    }
    spent = sum(per_stat.values())
    available = status_points_at(character.base_level, settings)
    return StatPointAudit(
        available=available,
        spent=spent,
        per_stat=per_stat,
        consistent=spent <= available,
    )


# -------------------------------------------------------------- breakpoints


def _aspd_for_agi(character: Character, job: JobInfo, agi: int, settings: Settings | None) -> int:
    """ASPD que a build teria com esse AGI base."""
    probe = character.model_copy(deep=True)
    probe.stats.agi = agi
    stats = total_stats(probe, job)
    from .engine import _max_aspd_for  # import local: só a análise precisa do teto

    return aspd_points(
        stats,
        job,
        weapon=probe.weapon_type,
        ranged=probe.weapon_type in RANGED_WEAPONS,
        shield=probe.has_shield,
        offhand=probe.left_hand.weapon_type if probe.is_dual_wielding else None,
        bonuses=probe.equipment_bonuses(),
        cap=_max_aspd_for(job.key),
    )


def find_breakpoints(
    character: Character,
    derived: DerivedStats,
    settings: Settings | None = None,
    *,
    search_range: int = 30,
) -> list[Breakpoint]:
    """Próximos limiares úteis em AGI (ASPD) e DEX/INT (conjuração)."""
    job = resolve_job(character.job, settings)
    out: list[Breakpoint] = []
    cap = stat_cap(job.key)

    # --- ASPD por AGI ---
    if derived.aspd < derived.aspd_cap:
        current_agi = character.stats.agi
        current_aspd = derived.aspd
        cost = 0
        for agi in range(current_agi + 1, min(current_agi + search_range, cap) + 1):
            cost += status_point_cost(agi - 1)
            new_aspd = _aspd_for_agi(character, job, agi, settings)
            if new_aspd > current_aspd:
                out.append(
                    Breakpoint(
                        stat="agi",
                        from_value=current_agi,
                        to_value=agi,
                        cost_points=cost,
                        effect=f"ASPD {current_aspd} → {new_aspd} "
                        f"({derived.attacks_per_second} → "
                        f"{round(1000 / (2 * (2000 - new_aspd * 10)), 3)} ataques/s)",
                    )
                )
                break

    # --- Conjuração variável ---
    # Atenção: o teto de atributo (130) impede chegar a DEX*2 + INT = 530 só com
    # pontos — o máximo é 390. Conjuração instantânea sempre depende de redução
    # vinda de equipamento. Por isso o alvo aqui é "quanto de redução a mais",
    # e não "quantos pontos até o instantâneo".
    if not derived.instant_cast:
        atual = derived.variable_cast_reduction
        for stat, valor_base in (("dex", character.stats.dex), ("int", character.stats.int_)):
            passo = 0
            custo = 0
            for novo in range(valor_base + 1, cap + 1):
                custo += status_point_cost(novo - 1)
                passo = novo
                total = dict(derived.total_stats)
                total[stat] += novo - valor_base
                nova = min(1.0, ((total["dex"] * 2 + total["int"]) / 530) ** 0.5)
                if nova - atual >= 0.02:
                    out.append(
                        Breakpoint(
                            stat=stat,
                            from_value=valor_base,
                            to_value=passo,
                            cost_points=custo,
                            effect=(
                                f"redução de conjuração variável {atual:.1%} → {nova:.1%} "
                                "(a diferença até 100% precisa vir de equipamento)"
                            ),
                        )
                    )
                    break

    return out


# ----------------------------------------------------------------- validação


def validate(
    character: Character,
    derived: DerivedStats,
    audit: StatPointAudit,
    settings: Settings | None = None,
) -> list[Finding]:
    """Erros de montagem e desperdícios, independentes do objetivo."""
    findings: list[Finding] = []
    job = resolve_job(character.job, settings)
    cap = stat_cap(job.key)

    # --- Consistência da entrada ---
    if not audit.consistent:
        findings.append(
            Finding(
                severity="critico",
                category="pontos",
                title="Mais pontos gastos do que o nível concede",
                detail=(
                    f"A build usa {audit.spent} pontos, mas o nível base "
                    f"{character.base_level} dá {audit.available}. "
                    "A causa mais comum é ter informado os atributos **totais** "
                    "(com bônus de classe e equipamento) em vez dos **base** — "
                    "na janela de status, é o número da esquerda."
                ),
                gain=f"{audit.spent - audit.available} pontos de diferença",
            )
        )
    elif audit.remaining >= 10:
        findings.append(
            Finding(
                severity="aviso",
                category="pontos",
                title=f"{audit.remaining} pontos de atributo não distribuídos",
                detail="Sobra de pontos é o ganho mais barato que existe. Veja os breakpoints sugeridos.",
                gain=f"{audit.remaining} pontos livres",
            )
        )

    # --- Tetos de atributo ---
    for name, value in character.stats.as_dict().items():
        if value > cap:
            findings.append(
                Finding(
                    severity="critico",
                    category="pontos",
                    title=f"{_STAT_LABELS[name]} base acima do teto da classe",
                    detail=f"{_STAT_LABELS[name]} base {value} passa do teto {cap} de {job.key}.",
                )
            )
    for name, value in character.traits.as_dict().items():
        if value > TRAIT_CAP:
            findings.append(
                Finding(
                    severity="critico",
                    category="pontos",
                    title=f"{name.upper()} acima do teto de trait",
                    detail=f"O teto de atributo de trait é {TRAIT_CAP}.",
                )
            )

    # --- Nível de classe ---
    if character.job_level < job.max_job_level:
        missing = job.max_job_level - character.job_level
        bonus_now = job.cumulative_job_bonus(character.job_level)
        bonus_max = job.cumulative_job_bonus(job.max_job_level)
        delta = {
            k: bonus_max[k] - bonus_now[k]
            for k in bonus_max
            if bonus_max[k] - bonus_now[k] > 0
        }
        if delta:
            pretty = ", ".join(f"+{v} {k.upper()}" for k, v in delta.items())
            findings.append(
                Finding(
                    severity="dica",
                    category="progressao",
                    title=f"Faltam {missing} níveis de classe",
                    detail=f"Chegando ao nível de classe {job.max_job_level} você ganha {pretty} de graça.",
                    gain=pretty,
                )
            )

    # --- Equipamento ---
    weapon = character.right_hand
    if weapon is None:
        findings.append(
            Finding(
                severity="critico",
                category="equipamento",
                title="Sem arma na mão direita",
                detail="Os cálculos de ATK e ASPD estão usando o valor de mão nua.",
            )
        )
    elif weapon.weapon_type in TWO_HANDED_WEAPONS and character.left_hand is not None:
        findings.append(
            Finding(
                severity="critico",
                category="equipamento",
                title="Arma de duas mãos com item na mão esquerda",
                detail=f"{weapon.name} ({weapon.weapon_type.value}) ocupa as duas mãos.",
            )
        )

    for slot in _CORE_SLOTS:
        if character.item_in(slot) is None:
            findings.append(
                Finding(
                    severity="aviso",
                    category="equipamento",
                    title=f"Nada equipado em {_SLOT_LABELS[slot]}",
                    detail="Slot vazio é ganho garantido, mesmo com item barato.",
                )
            )

    free_slots = sum(item.free_slots() for item in character.equipment)
    if free_slots:
        detalhe = ", ".join(
            f"{item.name} ({item.free_slots()})"
            for item in character.equipment
            if item.free_slots()
        )
        findings.append(
            Finding(
                severity="aviso",
                category="equipamento",
                title=f"{free_slots} slot(s) de carta vazio(s)",
                detail=f"Sem carta em: {detalhe}.",
                gain=f"{free_slots} carta(s)",
            )
        )

    low_refine = [
        item for item in character.equipment
        if item.refine < 7 and item.slot in (EquipSlot.RIGHT_HAND, EquipSlot.ARMOR)
    ]
    if low_refine:
        nomes = ", ".join(f"{i.name} (+{i.refine})" for i in low_refine)
        findings.append(
            Finding(
                severity="dica",
                category="equipamento",
                title="Refino baixo em peça principal",
                detail=f"{nomes}. No Renewal o refino de arma soma ATK e MATK direto no lado direito.",
            )
        )

    # --- Desperdícios numéricos ---
    if derived.aspd >= derived.aspd_cap:
        findings.append(
            Finding(
                severity="aviso",
                category="desperdicio",
                title=f"ASPD no teto ({derived.aspd_cap})",
                detail="Mais AGI ou bônus de ASPD não aumentam mais a velocidade de ataque.",
            )
        )
    if derived.instant_cast:
        total = derived.total_stats
        excess = total["dex"] * 2 + total["int"] - 530
        if excess >= 20:
            findings.append(
                Finding(
                    severity="dica",
                    category="desperdicio",
                    title="DEX/INT além do necessário para cast instantâneo",
                    detail=(
                        f"DEX*2 + INT = {total['dex'] * 2 + total['int']}, e 530 já zera a "
                        "conjuração variável. O excedente só vale por ATK/MATK, HIT e MDEF."
                    ),
                    gain=f"{excess} pontos de status acima do limiar",
                )
            )

    return findings


# ----------------------------------------------------------------- sugestões


def _target_notes(derived: DerivedStats, target: TargetMonster) -> list[Finding]:
    """Comparações diretas contra um alvo do Divine Pride."""
    out: list[Finding] = []
    if target.flee is not None:
        # Chance de acerto = 100 - (flee_alvo - hit) ; acima de 95% é o teto usual.
        chance = min(100, max(5, 100 - (target.flee - derived.hit)))
        severity = "critico" if chance < 80 else "aviso" if chance < 95 else "dica"
        out.append(
            Finding(
                severity=severity,
                category="alvo",
                title=f"Chance de acerto em {target.name}: ~{chance}%",
                detail=(
                    f"Seu HIT é {derived.hit} e o FLEE de {target.name} é {target.flee}. "
                    + ("Suba DEX ou HIT de equipamento até fechar 95%." if chance < 95 else "Está no teto prático.")
                ),
                gain=f"faltam {max(0, target.flee - derived.hit - 5)} de HIT para 95%" if chance < 95 else None,
            )
        )
    if target.hit is not None:
        esquiva = min(95, max(5, 100 - (target.hit - derived.flee)))
        out.append(
            Finding(
                severity="dica",
                category="alvo",
                title=f"Esquiva contra {target.name}: ~{100 - esquiva}% de chance de apanhar",
                detail=f"Seu FLEE é {derived.flee} e o HIT de {target.name} é {target.hit}.",
            )
        )
    if target.race is not None:
        out.append(
            Finding(
                severity="dica",
                category="alvo",
                title=f"{target.name} é da raça {target.race.value}",
                detail=(
                    "Cartas e encantamentos com bônus de dano por raça são o ganho percentual "
                    "mais barato contra um alvo fixo."
                ),
            )
        )
    if target.element is not None:
        nivel = f" nível {target.element_level}" if target.element_level else ""
        out.append(
            Finding(
                severity="dica",
                category="alvo",
                title=f"Elemento de {target.name}: {target.element.value}{nivel}",
                detail="Escolha o elemento da arma (ou a carta de elemento) pela tabela de fraquezas.",
            )
        )
    if target.defense is not None and target.defense >= 100:
        out.append(
            Finding(
                severity="aviso",
                category="alvo",
                title=f"DEF alta em {target.name} ({target.defense})",
                detail="Contra DEF alta, ignorar defesa rende mais do que somar ATK cru.",
            )
        )
    return out


def suggest(
    character: Character,
    derived: DerivedStats,
    audit: StatPointAudit,
    breakpoints: list[Breakpoint],
    *,
    goal: Goal | None = None,
    target: TargetMonster | None = None,
) -> list[Finding]:
    """Recomendações guiadas pelo objetivo, com o número que as justifica."""
    goal = goal or character.goal
    out: list[Finding] = []

    for bp in breakpoints:
        cabe = " (cabe nos pontos livres)" if bp.cost_points <= audit.remaining else ""
        out.append(
            Finding(
                severity="dica",
                category="breakpoint",
                title=f"{_STAT_LABELS.get(bp.stat, bp.stat.upper())} {bp.from_value} → {bp.to_value}"
                f" custa {bp.cost_points} pontos{cabe}",
                detail=bp.effect,
                gain=bp.effect,
            )
        )

    if goal is None:
        out.append(
            Finding(
                severity="dica",
                category="objetivo",
                title="Sem objetivo declarado",
                detail=(
                    "Informe um objetivo (leveling, farm, mvp, pvp, woe, tank, support) "
                    "para receber recomendações específicas em vez de genéricas."
                ),
            )
        )
        return out

    magico = derived.matk_right > derived.atk_right

    if goal in (Goal.MVP, Goal.PVP, Goal.WOE):
        if derived.hit < 300:
            out.append(
                Finding(
                    severity="aviso",
                    category=goal.value,
                    title=f"HIT baixo para {goal.value} ({derived.hit})",
                    detail="Alvos de MVP e jogadores em WoE têm FLEE alto; errar ataque anula qualquer ATK.",
                )
            )
        if goal in (Goal.PVP, Goal.WOE):
            if derived.res < 100 or derived.mres < 100:
                out.append(
                    Finding(
                        severity="aviso",
                        category=goal.value,
                        title=f"Resistências baixas (RES {derived.res} / MRES {derived.mres})",
                        detail=(
                            "Em PvP/WoE moderno, RES e MRES (via STA e WIS, ou equipamento) "
                            "reduzem dano de forma muito mais eficiente do que DEF pura."
                        ),
                    )
                )
            if derived.max_hp < 100_000:
                out.append(
                    Finding(
                        severity="dica",
                        category=goal.value,
                        title=f"HP máximo {derived.max_hp}",
                        detail="VIT e % de HP máximo compram sobrevivência linear; considere antes de mais dano.",
                    )
                )

    if goal in (Goal.FARM, Goal.LEVELING):
        if not magico and derived.aspd < derived.aspd_cap - 5:
            out.append(
                Finding(
                    severity="aviso",
                    category=goal.value,
                    title=f"ASPD {derived.aspd} (teto {derived.aspd_cap})",
                    detail=(
                        "Para farm e leveling em ataque físico, ASPD é o multiplicador mais direto: "
                        f"hoje são {derived.attacks_per_second} ataques/s."
                    ),
                )
            )
        if magico and not derived.instant_cast:
            falta = 1 - derived.variable_cast_total_reduction
            out.append(
                Finding(
                    severity="aviso",
                    category=goal.value,
                    title="Sem conjuração instantânea",
                    detail=(
                        f"Os atributos entregam {derived.variable_cast_reduction:.0%} de redução e o "
                        f"total está em {derived.variable_cast_total_reduction:.0%}. "
                        f"Faltam {falta:.0%}, e isso tem de vir de equipamento com redução de "
                        "conjuração variável — o teto de atributo não deixa fechar só com DEX/INT."
                    ),
                    gain=f"{falta:.0%} de redução de cast faltando",
                )
            )
        if derived.max_sp < 1000 and magico:
            out.append(
                Finding(
                    severity="dica",
                    category=goal.value,
                    title=f"SP máximo {derived.max_sp}",
                    detail="Farm mágico trava por SP antes de travar por dano; INT e % de SP ajudam a manter o ritmo.",
                )
            )

    if goal is Goal.TANK:
        out.append(
            Finding(
                severity="dica",
                category=goal.value,
                title=f"HP {derived.max_hp}, DEF {derived.def_display}, RES {derived.res} / MRES {derived.mres}",
                detail=(
                    "Para tankar, a ordem de prioridade costuma ser: reduções percentuais > "
                    "RES/MRES > HP máximo > DEF plana."
                ),
            )
        )
        if derived.soft_def < 100:
            out.append(
                Finding(
                    severity="aviso",
                    category=goal.value,
                    title=f"DEF de VIT baixa ({derived.soft_def})",
                    detail="O lado esquerdo do DEF vem de VIT, nível e AGI — é o que reduz dano por acerto.",
                )
            )

    if goal is Goal.SUPPORT:
        if not derived.instant_cast:
            falta = 1 - derived.variable_cast_total_reduction
            out.append(
                Finding(
                    severity="critico",
                    category=goal.value,
                    title="Suporte sem conjuração instantânea",
                    detail=(
                        f"A redução total está em {derived.variable_cast_total_reduction:.0%} "
                        f"(sendo {derived.variable_cast_reduction:.0%} de DEX/INT). "
                        f"Faltam {falta:.0%} de redução de conjuração variável em equipamento. "
                        "Para suporte, curar no tempo certo vale mais do que curar mais."
                    ),
                    gain=f"{falta:.0%} de redução de cast faltando",
                )
            )
        out.append(
            Finding(
                severity="dica",
                category=goal.value,
                title=f"SP máximo {derived.max_sp}",
                detail="Suporte gasta SP continuamente; INT ajuda em cura, SP e MDEF ao mesmo tempo.",
            )
        )

    if target is not None:
        out.extend(_target_notes(derived, target))

    return out


# -------------------------------------------------------------- ponto de entrada


def analyze(
    character: Character,
    *,
    goal: Goal | None = None,
    target: TargetMonster | None = None,
    settings: Settings | None = None,
) -> BuildReport:
    """Roda o pipeline inteiro: cálculo → auditoria → breakpoints → sugestões."""
    derived = compute(character, settings)
    audit = stat_point_audit(character, settings)
    breakpoints = find_breakpoints(character, derived, settings)
    findings = validate(character, derived, audit, settings)
    findings += suggest(
        character, derived, audit, breakpoints, goal=goal or character.goal, target=target
    )
    order = {"critico": 0, "aviso": 1, "dica": 2}
    findings.sort(key=lambda f: order[f.severity])
    return BuildReport(
        character=character,
        derived=derived,
        audit=audit,
        breakpoints=breakpoints,
        findings=findings,
        target=target,
    )


def simulate(
    character: Character,
    changes: dict[str, Any],
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Compara a build atual com uma versão alterada.

    `changes` aceita:

    * `stats`  — atributos base a sobrescrever, ex. `{"agi": 120, "str": 100}`
    * `traits` — idem para atributos de trait
    * `refine` — `{"nome ou slot do item": novo_refino}`
    * `job_level` / `base_level` — novos níveis

    Devolve os dois conjuntos de números e o que mudou entre eles.
    """
    modified = character.model_copy(deep=True)

    for name, value in (changes.get("stats") or {}).items():
        key = "str_" if name == "str" else "int_" if name == "int" else name
        if not hasattr(modified.stats, key):
            raise ValueError(f"Atributo desconhecido: {name}")
        setattr(modified.stats, key, int(value))
    for name, value in (changes.get("traits") or {}).items():
        if not hasattr(modified.traits, name):
            raise ValueError(f"Trait desconhecido: {name}")
        setattr(modified.traits, name, int(value))
    for ref, value in (changes.get("refine") or {}).items():
        alvo = ref.strip().casefold()
        for item in modified.equipment:
            if item.name.casefold() == alvo or item.slot.value == alvo:
                item.refine = int(value)
                break
        else:
            raise ValueError(f"Item não encontrado para refinar: {ref!r}")
    if "job_level" in changes:
        modified.job_level = int(changes["job_level"])
    if "base_level" in changes:
        modified.base_level = int(changes["base_level"])

    before = compute(character, settings)
    after = compute(modified, settings)

    campos = (
        "atk_left", "atk_right", "matk_left", "matk_right", "hit", "flee", "crit",
        "soft_def", "hard_def", "soft_mdef", "aspd", "attacks_per_second",
        "max_hp", "max_sp", "patk", "smatk", "res", "mres",
        "variable_cast_total_reduction",
    )
    diff: dict[str, dict[str, Any]] = {}
    for campo in campos:
        antes = getattr(before, campo)
        depois = getattr(after, campo)
        if antes != depois:
            diff[campo] = {
                "antes": antes,
                "depois": depois,
                "delta": round(depois - antes, 4) if isinstance(antes, (int, float)) else None,
            }

    antes_audit = stat_point_audit(character, settings)
    depois_audit = stat_point_audit(modified, settings)

    return {
        "mudancas": changes,
        "diferencas": diff,
        "pontos": {
            "antes": {"gastos": antes_audit.spent, "sobrando": antes_audit.remaining},
            "depois": {"gastos": depois_audit.spent, "sobrando": depois_audit.remaining},
            "consistente": depois_audit.consistent,
        },
        "aviso": (
            None
            if depois_audit.consistent
            else "A build alterada gasta mais pontos do que o nível permite."
        ),
    }


__all__ = [
    "Breakpoint",
    "BuildReport",
    "StatPointAudit",
    "analyze",
    "find_breakpoints",
    "simulate",
    "stat_point_audit",
    "suggest",
    "validate",
]
