"""Which requirements and decisions the changes touch (rule ``diff-traceability@1``) — only through what
the architecture and the decision records state, never by reading their words.

**Requirements**, each with exactly one relation (the first that holds):

1. ``directly_changed``: a trace to it was added or removed — an element carrying a reference to it was
   added or removed, or an element's reference to it changed (added, removed, another version);
2. ``element_changed``: an element traced to it (in either state) changed otherwise;
3. ``potential``: nothing traced to it changed, but the validation engine's verdict on it differs
   between the states (``satisfied`` → ``violated`` is said as the engine said it, never more);
4. for a requirement the person scoped the comparison to, and only then: ``no_relationship`` (verdicts
   known and equal) or ``undetermined`` (a verdict missing in a state).

A requirement referenced by a state but not readable (deleted, another project) is not described: it
is listed as an unknown, by id.

**Decisions**: an ADR in force (``proposed`` or ``accepted``) whose related elements — its own list,
or the decision references of either state — include a changed element *may require review*. Rejected
and superseded decisions are not in force and are not raised.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.traceability import RequirementRef
from core.domain.architecture_diff.changes import Change, SemanticDiff
from core.domain.architecture_diff.impacts import DecisionImpact, RequirementImpact
from core.domain.architecture_diff.values import RequirementRelation
from core.domain.decisions.entities import Decision, DecisionStatus
from core.domain.requirements.entities import Requirement

RULE = "diff-traceability@1"
IN_FORCE = frozenset({DecisionStatus.PROPOSED, DecisionStatus.ACCEPTED})
R = RequirementRelation
ORDER = {relation: index for index, relation in enumerate(R)}
ABSENT = object()  # no trace (distinct from a trace to "the requirement as it evolves": version None)

type Traces = dict[uuid.UUID, dict[str, int | None]]  # requirement → element id → version traced
type Verdicts = Mapping[uuid.UUID, str]


@dataclass(frozen=True, slots=True)
class Traceability:
    requirements: tuple[RequirementImpact, ...]
    decisions: tuple[DecisionImpact, ...]
    unknowns: tuple[str, ...]  # what could not be read, said


def _traces(ir: ArchitectureIR) -> Traces:
    """Every requirement reference of the state, by element (the architecture's own: "architecture")."""
    found: Traces = defaultdict(dict)
    holders: list[tuple[str, tuple[RequirementRef, ...]]] = [
        ("architecture", ir.requirement_refs),
        *((n.id, n.requirement_refs) for n in ir.nodes),
        *((c.id, c.requirement_refs) for c in ir.connections),
        *((a.id, a.requirement_refs) for a in ir.assumptions),
    ]
    for element_id, refs in holders:
        for ref in refs:
            found[ref.requirement_id][element_id] = ref.version
    return found


def _relation(
    before: dict[str, int | None], after: dict[str, int | None], changed: Mapping[str, Change]
) -> tuple[RequirementRelation | None, tuple[str, ...]]:
    elements = set(before) | set(after)
    direct = sorted(e for e in elements if e in changed and before.get(e, ABSENT) != after.get(e, ABSENT))
    if direct:
        return R.DIRECTLY_CHANGED, tuple(direct)
    linked = sorted(elements & set(changed))
    return (R.ELEMENT_CHANGED, tuple(linked)) if linked else (None, ())


def _number(reference: str) -> int:
    digits = reference.removeprefix("REQ-")
    return int(digits) if digits.isdigit() else 0


def requirement_impacts(
    base: ArchitectureIR,
    target: ArchitectureIR,
    semantic: SemanticDiff,
    requirements: Mapping[uuid.UUID, Requirement],
    verdicts: tuple[Verdicts, Verdicts] | None = None,
    scope: tuple[uuid.UUID, ...] | None = None,
) -> tuple[tuple[RequirementImpact, ...], tuple[str, ...]]:
    """The requirements the changes touch; with ``scope``, also each scoped requirement's state."""
    before, after = _traces(base), _traces(target)
    changed = {c.element_id: c for c in semantic.changes}
    base_verdicts, target_verdicts = verdicts or ({}, {})
    candidates = set(before) | set(after) | set(base_verdicts) | set(target_verdicts) | set(scope or ())
    if scope is not None:
        candidates &= set(scope)
    impacts: list[RequirementImpact] = []
    unknowns: list[str] = []
    for requirement_id in sorted(candidates, key=str):
        traced_before, traced_after = before.get(requirement_id, {}), after.get(requirement_id, {})
        relation, elements = _relation(traced_before, traced_after, changed)
        first, second = base_verdicts.get(requirement_id), target_verdicts.get(requirement_id)
        if relation is None and first is not None and second is not None and first != second:
            relation = R.POTENTIAL
        if relation is None and scope is not None:
            relation = R.NO_RELATIONSHIP if first is not None and first == second else R.UNDETERMINED
        if relation is None:
            continue
        requirement = requirements.get(requirement_id)
        if requirement is None:
            unknowns.append(f"Requirement {requirement_id} is referenced but cannot be read here.")
            continue
        pinned = [v for v in (*traced_after.values(), *traced_before.values()) if v is not None]
        content = requirement.content
        impacts.append(
            RequirementImpact(
                requirement_id,
                requirement.reference,
                pinned[0] if pinned else requirement.version,
                content.title,
                content.statement,
                relation,
                elements,
                tuple(changed[e].id for e in elements),
                first,
                second,
            )
        )
    impacts.sort(key=lambda i: (ORDER[i.relation], _number(i.reference)))
    return tuple(impacts), tuple(unknowns)


def decision_impacts(
    base: ArchitectureIR, target: ArchitectureIR, semantic: SemanticDiff, decisions: Iterable[Decision]
) -> tuple[DecisionImpact, ...]:
    """The decisions in force whose elements changed: they may require review."""
    changed = {c.element_id: c for c in semantic.changes}
    referenced: dict[uuid.UUID, set[str]] = defaultdict(set)
    for ir in (base, target):
        for ref in ir.decisions:
            referenced[ref.decision_id] |= set(ref.subject_ids)
    found = []
    for decision in sorted(decisions, key=lambda d: d.number):
        if decision.status not in IN_FORCE:
            continue
        related = set(decision.related_element_ids) | referenced.get(decision.id, set())
        elements = sorted(related & set(changed))
        if elements:
            found.append(
                DecisionImpact(
                    decision.id,
                    f"ADR-{decision.number}",
                    decision.title,
                    decision.status.value,
                    tuple(elements),
                    tuple(changed[e].id for e in elements),
                )
            )
    return tuple(found)


def traceability(
    base: ArchitectureIR,
    target: ArchitectureIR,
    semantic: SemanticDiff,
    requirements: Mapping[uuid.UUID, Requirement],
    decisions: Iterable[Decision],
    verdicts: tuple[Verdicts, Verdicts] | None = None,
    scope: tuple[uuid.UUID, ...] | None = None,
) -> Traceability:
    impacts, unknowns = requirement_impacts(base, target, semantic, requirements, verdicts, scope)
    return Traceability(impacts, decision_impacts(base, target, semantic, decisions), unknowns)
