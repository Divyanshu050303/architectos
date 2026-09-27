"""Trade-offs: each candidate's consequences, one row per dimension, and the alternatives for each goal
side by side — evidence-backed, never scored, weighted, ranked or declared optimal.

**Modeled rows** come from the candidate's impacts (the engines' own deltas and finding changes):

- ``capacity``: the system's bottleneck count and highest utilization, baseline versus candidate;
- ``cost``: the modeled monthly cost difference (a cost increase ``worsens``), and whether the
  candidate's modeled total stays within a cost-ceiling goal;
- ``reliability``: the modeled availability differences;
- ``security``, ``observability``: the findings the candidate resolves and introduces.

A dimension an engine could not establish is ``unknown`` (with the reason and what is missing, and
what the rule states about it); a candidate found invalid or unsupported is ``not_evaluated``.

**Rule rows** follow documented rules: configuration changes add no component or connection
(``dependencies``: unchanged) and are reverted by restoring the changed properties
(``reversibility``, with what reverting involves).

**Considerations** are for human review — no model decides them: the operational complexity, the
migration effort and the failure modes the rule names, and the expertise each category of change
calls for (``EXPERTISE``).
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any

from core.domain.engine_results import Evidence

from .candidates import Candidate, Consequence, Effect, Impact
from .goals import EvolutionGoal
from .values import Basis, CandidateCategory, Direction, EvidenceSource, GoalType

B, D, S = Basis, Direction, EvidenceSource
EXPERTISE = {
    CandidateCategory.SCALING: "Running and observing more (or larger) instances, and capacity planning.",
    CandidateCategory.REDUNDANCY: "Operating replicated components: state replication and failover.",
    CandidateCategory.SECURITY_CONTROL: "Managing certificates or encryption keys, and rolling them out.",
    CandidateCategory.INSTRUMENTATION: "Operating the pipeline that collects and stores the telemetry.",
}
UNKNOWN_STATES = frozenset({"unsupported", "failed"})


def _number(raw: str | None) -> Decimal | None:
    try:
        return Decimal(raw) if raw is not None else None
    except InvalidOperation:
        return None


def _direction(differences: Iterable[Decimal], *, higher_is_better: bool) -> Direction:
    signs = {(d > 0) == higher_is_better for d in differences if d != 0}
    if not signs:
        return D.UNCHANGED
    if len(signs) == 2:
        return D.MIXED
    return D.IMPROVES if signs.pop() else D.WORSENS


def _evidence(impact: Impact) -> tuple[Evidence, ...]:
    refs = [Evidence("engine", impact.source.value), Evidence("state", impact.state)]
    if impact.model_version:
        refs.append(Evidence("model_version", impact.model_version))
    if impact.candidate_fingerprint:
        refs.append(Evidence("candidate_fingerprint", impact.candidate_fingerprint))
    return tuple(refs)


def _stated(candidate: Candidate, dimension: str) -> str:
    stated = [e.statement for e in (*candidate.benefits, *candidate.tradeoffs) if e.dimension == dimension]
    return f" The rule states: {' '.join(stated)}" if stated else ""


def _unknown(candidate: Candidate, impact: Impact) -> Consequence:
    if impact.state == "not_evaluated":
        return Consequence(
            impact.dimension.value,
            D.NOT_EVALUATED,
            B.MODELED,
            f"Not evaluated: {impact.reason}.",
            _evidence(impact),
        )
    missing = f" Missing: {'; '.join(impact.missing)}." if impact.missing else ""
    return Consequence(
        impact.dimension.value,
        D.UNKNOWN,
        B.MODELED,
        f"The {impact.dimension.value} engine could not establish it ({impact.reason or impact.state})."
        + missing
        + _stated(candidate, impact.dimension.value),
        _evidence(impact),
    )


def _partial(impact: Impact) -> str:
    return " Partial: some of it could not be calculated." if impact.state == "partial" else ""


def _capacity(candidate: Candidate, impact: Impact) -> Consequence:
    system = {d.metric: d for d in impact.deltas if d.element_id == "system"}
    differences = [
        v
        for m in ("bottlenecks", "highest_utilization")
        if m in system and (v := _number(system[m].difference)) is not None
    ]
    shown = ", ".join(
        f"{m} {system[m].baseline} -> {system[m].candidate}"
        for m in ("bottlenecks", "highest_utilization")
        if m in system
    )
    if not differences and not shown:
        return Consequence(
            "capacity",
            D.UNCHANGED,
            B.MODELED,
            "No modeled capacity change." + _partial(impact),
            _evidence(impact),
        )
    return Consequence(
        "capacity",
        _direction(differences, higher_is_better=False),
        B.MODELED,
        f"Modeled {shown}." + _partial(impact),
        _evidence(impact),
    )


def _cost(candidate: Candidate, impact: Impact, goals: Iterable[EvolutionGoal]) -> Consequence:
    total = next((d for d in impact.deltas if (d.element_id, d.metric) == ("system", "monthly_cost")), None)
    if total is None:
        return Consequence(
            "cost", D.UNCHANGED, B.MODELED, "No modeled cost change." + _partial(impact), _evidence(impact)
        )
    difference = _number(total.difference)
    if not total.comparable or difference is None:
        note = f" ({total.note})" if total.note else ""
        return Consequence(
            "cost",
            D.UNKNOWN,
            B.MODELED,
            f"The modeled monthly cost cannot be compared{note}: {total.baseline} -> {total.candidate} "
            f"{total.unit}." + _stated(candidate, "cost"),
            _evidence(impact),
        )
    statement = (
        f"Modeled monthly cost {total.baseline} -> {total.candidate} {total.unit} ({total.difference})."
    )
    after = _number(total.candidate)
    for goal in goals:
        if (
            goal.type is GoalType.COST_CEILING
            and after is not None
            and total.unit == f"{goal.currency}/month"
        ):
            assert goal.amount is not None  # noqa: S101 -- required by the goal type
            within = "within" if after <= goal.amount else "above"
            statement += f" It is {within} the ceiling of {goal.amount} {goal.currency}/month."
    return Consequence(
        "cost",
        _direction([difference], higher_is_better=False),
        B.MODELED,
        statement + _partial(impact),
        _evidence(impact),
    )


def _reliability(candidate: Candidate, impact: Impact) -> Consequence:
    rows = [d for d in impact.deltas if "availability" in d.metric]
    differences = [v for d in rows if (v := _number(d.difference)) is not None]
    if not rows:
        return Consequence(
            "reliability",
            D.UNCHANGED,
            B.MODELED,
            "No modeled availability change." + _partial(impact),
            _evidence(impact),
        )
    shown = "; ".join(f"{d.element_id} {d.baseline} -> {d.candidate}" for d in rows)
    return Consequence(
        "reliability",
        _direction(differences, higher_is_better=True) if differences else D.UNKNOWN,
        B.MODELED,
        f"Modeled availability: {shown}."
        + _partial(impact)
        + ("" if differences else _stated(candidate, "reliability")),
        _evidence(impact),
    )


def _findings(impact: Impact) -> Consequence:
    resolved = sorted(c.code for c in impact.changes if c.kind == "resolved")
    introduced = sorted(c.code for c in impact.changes if c.kind == "introduced")
    if resolved and introduced:
        direction = D.MIXED
    elif resolved:
        direction = D.IMPROVES
    elif introduced:
        direction = D.WORSENS
    else:
        direction = D.UNCHANGED
    parts = [
        f"resolves {', '.join(resolved)}" if resolved else "",
        f"introduces {', '.join(introduced)}" if introduced else "",
    ]
    text = "; ".join(p for p in parts if p) or "changes no finding"
    return Consequence(
        impact.dimension.value, direction, B.MODELED, f"The candidate {text}.", _evidence(impact)
    )


def _modeled(candidate: Candidate, impact: Impact, goals: tuple[EvolutionGoal, ...]) -> Consequence:
    if impact.state in UNKNOWN_STATES or impact.state == "not_evaluated":
        return _unknown(candidate, impact)
    match impact.dimension:
        case S.CAPACITY:
            return _capacity(candidate, impact)
        case S.COST:
            return _cost(candidate, impact, goals)
        case S.RELIABILITY:
            return _reliability(candidate, impact)
        case _:
            return _findings(impact)


def _considered(dimension: str, effects: Iterable[Effect]) -> Consequence | None:
    stated = list(effects)
    if not stated:
        return None
    return Consequence(
        dimension,
        D.CONSIDERATION,
        B.CONSIDERATION,
        " ".join(e.statement for e in stated),
        tuple(Evidence(e.code, e.basis.value) for e in stated),
    )


def consequences(candidate: Candidate, goals: Iterable[EvolutionGoal] = ()) -> tuple[Consequence, ...]:
    """The candidate's trade-off table: modeled rows from its impacts, rule rows, considerations."""
    goals = tuple(goals)
    rows = [_modeled(candidate, i, goals) for i in candidate.impacts]
    properties = ", ".join(f"{c.element_id}.{c.property}" for c in candidate.changes)
    rows.append(
        Consequence(
            "dependencies",
            D.UNCHANGED,
            B.RULE,
            "Configuration changes of existing elements add no component or connection.",
        )
    )
    involved = [e.statement for e in candidate.migration]
    rows.append(
        Consequence(
            "reversibility",
            D.CONSIDERATION,
            B.RULE,
            f"Restoring {properties} to the baseline values reverts the configuration."
            + (f" Reverting may involve: {' '.join(involved)}" if involved else ""),
        )
    )
    optional = (
        _considered("operations", candidate.complexity),
        _considered("migration", candidate.migration),
        _considered("failure_modes", candidate.risks),
    )
    rows += [row for row in optional if row is not None]
    rows.append(Consequence("expertise", D.CONSIDERATION, B.CONSIDERATION, EXPERTISE[candidate.category]))
    return tuple(sorted(rows, key=lambda row: row.dimension))


def with_tradeoffs(candidate: Candidate, goals: Iterable[EvolutionGoal] = ()) -> Candidate:
    return replace(candidate, consequences=consequences(candidate, goals))


@dataclass(frozen=True, slots=True)
class Alternatives:
    """The candidates addressing one goal, side by side: each one's direction per dimension. The
    order is canonical (category, id) — not a ranking; nothing here chooses between them."""

    goal: str
    candidates: tuple[str, ...]
    table: tuple[tuple[str, str, Direction], ...]  # (candidate id, dimension, direction)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "candidates": list(self.candidates),
            "table": [{"candidate_id": c, "dimension": d, "direction": v.value} for c, d, v in self.table],
        }


def alternatives(candidates: Iterable[Candidate], goals: Iterable[EvolutionGoal]) -> tuple[Alternatives, ...]:
    ordered = sorted(candidates, key=lambda c: (c.category.value, c.id))
    found = []
    for goal in sorted(goals, key=lambda g: g.key):
        addressing = [c for c in ordered if goal.key in c.goals]
        found.append(
            Alternatives(
                goal.key,
                tuple(c.id for c in addressing),
                tuple((c.id, row.dimension, row.direction) for c in addressing for row in c.consequences),
            )
        )
    return tuple(found)
