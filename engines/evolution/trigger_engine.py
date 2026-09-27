"""Evidence-driven triggers: for each goal, the reasons the current evidence gives for evolving the
architecture, and what keeps the engine from deciding.

**Compatibility.** Each goal is evaluated by one engine (a requirement: by every engine that checks
it). The stored analysis of that engine (the one the request cites, or the latest) is ``current``
only when it analyzed the baseline's exact content (same content hash); otherwise it is ``stale``:
reported, never used. An engine without an analysis is ``missing``. A goal with no current evidence
is not evaluable. Nothing is recomputed here, and nothing is inferred from a finding's severity:
triggers carry no urgency.

**Goals.**

- ``increase_workload``: the capacity analysis must be at the goal's rate (the scaling options of
  another workload do not describe this goal). Its scaling options and the resources no model scales
  are triggers; none, in a completed analysis, means the goal is already met.
- ``cost_ceiling``: the cost analysis must be in the goal's currency. A complete total within the
  ceiling meets it; a total above it (or a known lower bound above it) is reported, with the largest
  cost driver — no rule reduces cost; an incomplete total below the ceiling cannot decide it.
- ``availability_objective``: the reliability findings about redundancy and failover are triggers;
  with none, every entry's modeled availability at or above the target meets it.
- ``recovery_objective``: the recovery findings are triggers; recovery times are evaluated only
  against requirement objectives, so without such findings the goal is not evaluable.
- ``address_finding``: the finding, by its stable id, in the current analysis of its engine; absent
  there, it is already addressed.
- ``observability_coverage``: the observability findings of that dimension.
- ``satisfy_requirement``: the verdicts the engines reached for the requirement; its violated
  findings are triggers. A requirement no current analysis checks is unsupported.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from core.architecture_ir.model import ArchitectureIR
from core.domain.evolution.candidates import MAX_ITEMS, BaselineRef, EvidenceRef
from core.domain.evolution.evidence import EvidenceItem, StoredAnalysis
from core.domain.evolution.goals import EvolutionGoal
from core.domain.evolution.results import EvolutionFinding, FindingType
from core.domain.evolution.triggers import Trigger, TriggerKind
from core.domain.evolution.values import EvidenceSource, EvidenceState, GoalType
from core.domain.reliability.results import FindingType as R
from core.domain.requirements.value_objects import decimal_to_str

S, F, K = EvidenceSource, FindingType, TriggerKind
REQUIREMENT_SOURCES = (S.RELIABILITY, S.SECURITY, S.OBSERVABILITY, S.VALIDATION)
AVAILABILITY_TYPES = frozenset(
    t.value
    for t in (
        R.SINGLE_POINT_OF_FAILURE,
        R.NO_REDUNDANCY,
        R.CRITICAL_DEPENDENCY_WITHOUT_ALTERNATIVE,
        R.REDUNDANCY_WITHOUT_FAILURE_DOMAIN_SEPARATION,
        R.POTENTIAL_CORRELATED_FAILURE,
        R.INCONSISTENT_REDUNDANCY,
        R.MISSING_FAILOVER,
        R.AVAILABILITY_BELOW_OBJECTIVE,
        R.REDUNDANCY_BELOW_OBJECTIVE,
    )
)
RECOVERY_TYPES = frozenset(
    t.value for t in (R.RECOVERY_EXCEEDS_OBJECTIVE, R.DATA_LOSS_EXCEEDS_OBJECTIVE, R.MISSING_FAILOVER)
)


@dataclass(frozen=True, slots=True)
class TriggerEvaluation:
    triggers: tuple[Trigger, ...]
    findings: tuple[EvolutionFinding, ...]
    evidence: tuple[EvidenceRef, ...]  # every analysis considered: current, stale and missing


class _Goal:
    """The triggers and findings of one goal, as they are found."""

    def __init__(self, goal: EvolutionGoal, scope: frozenset[str] | None, ir: ArchitectureIR) -> None:
        self.goal = goal
        self.scope = scope
        self.ir = ir
        self.triggers: list[Trigger] = []
        self.findings: list[EvolutionFinding] = []

    def finding(
        self,
        kind: FindingType,
        message: str,
        evidence: Iterable[EvidenceRef] = (),
        missing: Iterable[str] = (),
        elements: Iterable[str] = (),
    ) -> None:
        """One finding, its lists bounded: past ``MAX_ITEMS`` a canonical prefix is kept and the rest
        is counted, never dropped silently (large architectures stay analyzable)."""
        refs = sorted(set(evidence), key=lambda e: e.key)[:MAX_ITEMS]
        self.findings.append(
            EvolutionFinding(kind, message, self.goal.key, _bounded(elements), tuple(refs), _bounded(missing))
        )

    def in_scope(self, element_id: str) -> bool:
        if self.scope is None:
            return True
        connection = self.ir.connection(element_id)
        if connection is not None:
            return connection.source_id in self.scope or connection.target_id in self.scope
        return element_id in self.scope

    def trigger(self, analysis: StoredAnalysis, item: EvidenceItem) -> bool:
        """``item`` as a trigger of this goal, if in scope; False when outside the scope."""
        if not self.in_scope(item.element_id):
            return False
        self.triggers.append(
            Trigger(
                item.kind,
                analysis.source,
                item.code,
                self.goal.key,
                item.element_id,
                analysis.ref(EvidenceState.CURRENT, item.item),
                item.facts,
                item.message,
            )
        )
        return True

    def unknowns(self, analysis: StoredAnalysis, items: Iterable[EvidenceItem], what: str) -> None:
        """Items that only say something is not modeled: what would decide the goal, one finding per
        element concerned."""
        by_element: dict[str, list[EvidenceItem]] = {}
        for item in items:
            if self.in_scope(item.element_id):
                by_element.setdefault(item.element_id, []).append(item)
        for element, found in sorted(by_element.items()):
            self.finding(
                F.MISSING_EVIDENCE,
                f"The {analysis.source.value} analysis cannot establish {what} for {element}: the "
                "architecture does not declare enough.",
                [analysis.ref(EvidenceState.CURRENT, i.item) for i in found],
                [m for i in found for m in i.missing] or [f"{element}: {i.code}" for i in found],
                (element,),
            )


def evaluate(
    ir: ArchitectureIR,
    baseline: BaselineRef,
    goals: Iterable[EvolutionGoal],
    analyses: Mapping[EvidenceSource, StoredAnalysis],
    *,
    scope: Iterable[str] | None = None,
) -> TriggerEvaluation:
    """Deterministic: the same baseline, goals, analyses and scope give the same triggers and
    findings, in the same order."""
    scoped = frozenset(scope) if scope is not None else None
    evidence: dict[tuple[str, str, str], EvidenceRef] = {}
    triggers: list[Trigger] = []
    findings: list[EvolutionFinding] = []
    for goal in sorted(goals, key=lambda g: g.key):
        state = _Goal(goal, scoped, ir)
        sources = REQUIREMENT_SOURCES if goal.type is GoalType.SATISFY_REQUIREMENT else (goal.method,)
        current = _current(state, baseline, analyses, sources, evidence)
        if current:
            _GOALS[goal.type](state, current)
        elif goal.type is not GoalType.SATISFY_REQUIREMENT:
            state.finding(
                F.GOAL_NOT_EVALUABLE,
                f"No current {goal.method.value} analysis of revision {baseline.revision_number} exists: "
                "the goal cannot be evaluated.",
                missing=[f"a {goal.method.value} analysis of revision {baseline.revision_number}"],
            )
        else:
            _requirement(state, current)
        triggers += state.triggers
        findings += state.findings
    return TriggerEvaluation(
        tuple(sorted(set(triggers), key=lambda t: t.key)),
        tuple(sorted(set(findings), key=lambda f: f.sort_key)),
        tuple(sorted(evidence.values(), key=lambda e: e.key)),
    )


def _current(
    state: _Goal,
    baseline: BaselineRef,
    analyses: Mapping[EvidenceSource, StoredAnalysis],
    sources: Iterable[EvidenceSource],
    evidence: dict[tuple[str, str, str], EvidenceRef],
) -> dict[EvidenceSource, StoredAnalysis]:
    """The analyses of ``sources`` that describe the baseline; stale and missing ones reported."""
    found: dict[EvidenceSource, StoredAnalysis] = {}
    for source in sources:
        analysis = analyses.get(source)
        if analysis is None:
            ref = EvidenceRef(source, "none", EvidenceState.MISSING)
            evidence[ref.key] = ref
            state.finding(
                F.MISSING_EVIDENCE,
                f"There is no {source.value} analysis of this architecture.",
                (ref,),
                (f"a {source.value} analysis of revision {baseline.revision_number}",),
            )
            continue
        if analysis.content_hash != baseline.content_hash:
            ref = analysis.ref(EvidenceState.STALE)
            evidence[ref.key] = ref
            state.finding(
                F.STALE_EVIDENCE,
                f"The {source.value} analysis {analysis.analysis_id} is of revision "
                f"{analysis.revision_number}, whose content differs from revision "
                f"{baseline.revision_number}: it is not used.",
                (ref,),
                (f"a {source.value} analysis of revision {baseline.revision_number}",),
            )
            continue
        ref = analysis.ref(EvidenceState.CURRENT)
        evidence[ref.key] = ref
        found[source] = analysis
    return found


def _bounded(values: Iterable[str]) -> tuple[str, ...]:
    ordered = sorted(set(values))
    if len(ordered) <= MAX_ITEMS:
        return tuple(ordered)
    return (*ordered[: MAX_ITEMS - 1], f"… and {len(ordered) - MAX_ITEMS + 1} more")


def _number(raw: str | None) -> Decimal | None:
    try:
        return Decimal(raw) if raw is not None else None
    except InvalidOperation:
        return None


# --- one evaluation per goal type -------------------------------------------------------------------


def _workload(state: _Goal, current: dict[EvidenceSource, StoredAnalysis]) -> None:
    analysis = current[S.CAPACITY]
    assert state.goal.target is not None  # noqa: S101 -- required by the goal type
    goal = state.goal.target.canonical_quantity()
    rate, unit = _number(analysis.fact("workload.peak_rate")), analysis.fact("workload.unit")
    target = f"{decimal_to_str(goal.value)} {goal.unit}"
    if rate != goal.value or unit != goal.unit:
        at = f"{decimal_to_str(rate)} {unit}" if rate is not None else "an unknown rate"
        state.finding(
            F.GOAL_NOT_EVALUABLE,
            f"The capacity analysis is at {at}, not at the goal's {target}: its scaling options do not "
            "describe this goal.",
            (analysis.ref(EvidenceState.CURRENT),),
            (f"a capacity analysis of the baseline at {target}",),
        )
        return
    kinds = (K.SCALING_OPTION, K.SCALING_UNSUPPORTED)
    found = [state.trigger(analysis, i) for i in analysis.items if i.kind in kinds]
    if any(found):
        if analysis.status != "completed":
            _partial(state, analysis)
        return
    if analysis.status == "completed":
        state.finding(
            F.GOAL_ALREADY_MET,
            f"At {target}, the capacity analysis finds every modeled resource within its target.",
            (analysis.ref(EvidenceState.CURRENT),),
        )
    else:
        state.finding(
            F.GOAL_NOT_EVALUABLE,
            f"At {target}, the capacity analysis states no scaling need, but it is {analysis.status}: "
            "some components could not be calculated.",
            (analysis.ref(EvidenceState.CURRENT),),
            analysis.missing or ("the capacity inputs of the components not calculated",),
        )


def _partial(state: _Goal, analysis: StoredAnalysis) -> None:
    state.finding(
        F.MISSING_EVIDENCE,
        f"The {analysis.source.value} analysis is {analysis.status}: what it could not calculate may "
        "need changes it cannot show.",
        (analysis.ref(EvidenceState.CURRENT),),
        analysis.missing or (f"the inputs the {analysis.source.value} analysis lacked",),
    )


def _cost(state: _Goal, current: dict[EvidenceSource, StoredAnalysis]) -> None:
    analysis = current[S.COST]
    ref = analysis.ref(EvidenceState.CURRENT)
    ceiling, currency = state.goal.amount, state.goal.currency
    assert ceiling is not None  # noqa: S101 -- required by the goal type
    if analysis.fact("currency") != currency:
        state.finding(
            F.GOAL_NOT_EVALUABLE,
            f"The cost analysis is in {analysis.fact('currency')}, not {currency}: no currency is converted.",
            (ref,),
            (f"a cost analysis of the baseline in {currency}",),
        )
        return
    monthly, complete = _number(analysis.fact("monthly")), analysis.fact("complete") == "true"
    if monthly is None:
        state.finding(F.GOAL_NOT_EVALUABLE, "The cost analysis has no known total.", (ref,), analysis.missing)
        return
    known = f"{decimal_to_str(monthly)} {currency}/month"
    limit = f"{decimal_to_str(ceiling)} {currency}/month"
    if monthly > ceiling:
        driver = analysis.fact("largest_component")
        bound = "" if complete else " (a lower bound: some lines are unknown)"
        state.finding(
            F.NO_APPLICABLE_RULE,
            f"The modeled monthly cost {known}{bound} exceeds the ceiling {limit}"
            + (f"; the largest component is {driver}" if driver else "")
            + ". No evolution rule proposes a cost reduction: reducing cost is a design decision.",
            (ref,),
            elements=(driver,) if driver else (),
        )
    elif complete:
        state.finding(F.GOAL_ALREADY_MET, f"The modeled monthly cost {known} is within {limit}.", (ref,))
    else:
        state.finding(
            F.GOAL_NOT_EVALUABLE,
            f"The known monthly cost {known} is within {limit}, but some lines are unknown: it is a lower "
            "bound, so the goal cannot be decided.",
            (ref,),
            analysis.missing or ("the prices of the unknown cost lines",),
        )


def _availability(state: _Goal, current: dict[EvidenceSource, StoredAnalysis]) -> None:
    analysis = current[S.RELIABILITY]
    items = [i for i in analysis.items if i.code in AVAILABILITY_TYPES]
    found = [state.trigger(analysis, i) for i in items if i.evaluable]
    state.unknowns(analysis, [i for i in analysis.items if not i.evaluable], "availability")
    if any(found):
        return
    assert state.goal.target is not None  # noqa: S101 -- required by the goal type
    target = state.goal.target.canonical
    values = [
        v
        for f in analysis.facts
        if f.label.startswith("availability.") and (v := _number(f.value)) is not None
    ]
    if values and min(values) >= target:
        state.finding(
            F.GOAL_ALREADY_MET,
            f"Every entry's modeled availability is at least {decimal_to_str(target)}.",
            (analysis.ref(EvidenceState.CURRENT),),
        )
    elif values:
        state.finding(
            F.NO_APPLICABLE_RULE,
            f"The lowest modeled availability, {decimal_to_str(min(values))}, is below "
            f"{decimal_to_str(target)}, and no finding names a change a rule can propose.",
            (analysis.ref(EvidenceState.CURRENT),),
        )
    else:
        state.finding(
            F.GOAL_NOT_EVALUABLE,
            "No entry's availability could be modeled.",
            (analysis.ref(EvidenceState.CURRENT),),
            analysis.missing or ("the availability of the components on each request path",),
        )


def _recovery(state: _Goal, current: dict[EvidenceSource, StoredAnalysis]) -> None:
    analysis = current[S.RELIABILITY]
    found = [state.trigger(analysis, i) for i in analysis.items if i.code in RECOVERY_TYPES and i.evaluable]
    state.unknowns(analysis, [i for i in analysis.items if not i.evaluable], "recovery")
    if not any(found):
        state.finding(
            F.GOAL_NOT_EVALUABLE,
            "The Reliability Engine evaluates recovery time only against a requirement's objective; "
            "no recovery finding describes this goal.",
            (analysis.ref(EvidenceState.CURRENT),),
            ("a recovery-time requirement the Reliability Engine evaluates",),
        )


def _finding(state: _Goal, current: dict[EvidenceSource, StoredAnalysis]) -> None:
    assert state.goal.finding is not None  # noqa: S101 -- required by the goal type
    analysis = current[state.goal.finding.source]
    item = next((i for i in analysis.items if i.item == state.goal.finding.finding_id), None)
    if item is None:
        state.finding(
            F.GOAL_ALREADY_MET,
            f"The current {analysis.source.value} analysis does not report {state.goal.finding.finding_id}.",
            (analysis.ref(EvidenceState.CURRENT),),
        )
    elif not item.evaluable:
        state.unknowns(analysis, (item,), item.code)
    elif not state.trigger(analysis, item):
        state.finding(
            F.GOAL_NOT_EVALUABLE,
            f"{item.item} concerns {item.element_id}, outside the requested scope.",
            (analysis.ref(EvidenceState.CURRENT, item.item),),
            elements=(item.element_id,),
        )


def _coverage(state: _Goal, current: dict[EvidenceSource, StoredAnalysis]) -> None:
    analysis = current[S.OBSERVABILITY]
    assert state.goal.dimension is not None  # noqa: S101 -- required by the goal type
    items = [i for i in analysis.items if i.dimension == state.goal.dimension.value]
    found = [state.trigger(analysis, i) for i in items if i.evaluable]
    unknown = [i for i in items if not i.evaluable]
    state.unknowns(analysis, unknown, f"{state.goal.dimension.value} coverage")
    if not any(found) and not any(state.in_scope(i.element_id) for i in unknown):
        state.finding(
            F.GOAL_ALREADY_MET,
            f"The observability analysis reports no {state.goal.dimension.value} gap.",
            (analysis.ref(EvidenceState.CURRENT),),
        )


def _requirement(state: _Goal, current: dict[EvidenceSource, StoredAnalysis]) -> None:
    requirement = str(state.goal.requirement_id)
    checks = [(a, c) for a in current.values() for c in a.checks if c.requirement_id == requirement]
    if not checks:
        kind = F.GOAL_UNSUPPORTED if len(current) == len(REQUIREMENT_SOURCES) else F.GOAL_NOT_EVALUABLE
        state.finding(
            kind,
            f"No current analysis checks requirement {requirement}: "
            + (
                "no engine evaluates it."
                if kind is F.GOAL_UNSUPPORTED
                else "the analyses that might are missing or stale."
            ),
            [a.ref(EvidenceState.CURRENT) for a in current.values()],
        )
        return
    verdicts = {c.verdict for _, c in checks}
    for analysis in current.values():
        for item in analysis.items:
            if item.requirement_id == requirement and item.evaluable:
                state.trigger(analysis, item)
    if "not_verifiable" in verdicts:
        state.finding(
            F.MISSING_EVIDENCE,
            f"Requirement {requirement} cannot be verified from what the architecture declares.",
            [a.ref(EvidenceState.CURRENT) for a, c in checks if c.verdict == "not_verifiable"],
            [m for _, c in checks for m in c.missing] or [f"the facts requirement {requirement} depends on"],
        )
    if verdicts <= {"satisfied", "not_applicable"}:
        state.finding(
            F.GOAL_ALREADY_MET,
            f"Every check of requirement {requirement} is satisfied by modeled evidence.",
            [a.ref(EvidenceState.CURRENT) for a, _ in checks],
        )
    elif "violated" in verdicts and not state.triggers:
        state.finding(
            F.NO_APPLICABLE_RULE,
            f"Requirement {requirement} is violated, and no finding names a change a rule can propose.",
            [a.ref(EvidenceState.CURRENT) for a, c in checks if c.verdict == "violated"],
        )


_GOALS = {
    GoalType.INCREASE_WORKLOAD: _workload,
    GoalType.COST_CEILING: _cost,
    GoalType.AVAILABILITY_OBJECTIVE: _availability,
    GoalType.RECOVERY_OBJECTIVE: _recovery,
    GoalType.ADDRESS_FINDING: _finding,
    GoalType.OBSERVABILITY_COVERAGE: _coverage,
    GoalType.SATISFY_REQUIREMENT: _requirement,
}
