"""Requirements as reliability objectives: only machine-checkable ones, with their ids.

A project requirement in force, of type ``availability`` or ``reliability``, becomes an objective
when its structured constraint says exactly what to check:

- ``availability`` / ``uptime`` at least (``>=``, ``==``) or more than (``>``) a fraction;
- ``rto`` at most (``<=``, ``==``) or less than (``<``) a duration: a recovery-time objective;
- ``rpo`` likewise: a data-loss objective.

On what: the components that reference the requirement (``requirement_refs``), else the
components of its scope (``service``, ``api``, ``database``, ``queue``, ``data``), else, for
``system`` scope, the request paths (availability) or every relevant component. Everything else is
reported, never passed: a requirement stated only in words is ``not_verifiable`` (not
machine-checkable), and so is one no model checks (``durability``, a range, a percentile, a
``user`` or ``region`` scope, an availability ceiling); a scope with no such components is
``not_applicable``.
"""

from core.architecture_ir.topology import Topology
from core.domain.capacity.units import Quantity
from core.domain.reliability.analyses import Objective
from core.domain.reliability.results import ObjectiveKind, ObjectiveResult
from core.domain.requirements.entities import Requirement
from core.domain.requirements.enums import RequirementScope, RequirementType
from core.domain.requirements.value_objects import Operator, QuantityConstraint, decimal_to_str
from core.domain.validation.results import Verdict
from engines.validation.rules.requirements import SCOPE_KINDS

from .engine import OUT_OF_SCOPE

TYPES = frozenset({RequirementType.AVAILABILITY, RequirementType.RELIABILITY})
FLOORS = {Operator.AT_LEAST: False, Operator.EQUALS: False, Operator.MORE_THAN: True}
CEILINGS = {Operator.AT_MOST: False, Operator.EQUALS: False, Operator.LESS_THAN: True}
TIMED = {"rto": ObjectiveKind.RECOVERY_TIME, "rpo": ObjectiveKind.DATA_LOSS}


def key(requirement: Requirement) -> str:
    return f"requirement.{requirement.reference.lower()}"  # e.g. "requirement.req-12"


def _unsupported(
    requirement: Requirement, why: str, verdict: Verdict = Verdict.NOT_VERIFIABLE
) -> ObjectiveResult:
    constraint = requirement.content.constraint
    stated = requirement.content.title if constraint is None else _stated(constraint)
    return ObjectiveResult(
        key(requirement), ObjectiveKind.UNSUPPORTED, stated or requirement.reference, verdict, why,
        requirement_id=str(requirement.id),
    )  # fmt: skip


def _stated(constraint: object) -> str:
    if isinstance(constraint, QuantityConstraint):
        value = decimal_to_str(constraint.value)
        return f"{constraint.metric} {constraint.operator.value} {value} {constraint.unit.symbol}"
    return str(getattr(constraint, "metric", "constraint"))


def _scope(requirement: Requirement, topology: Topology) -> tuple[str, ...] | None:
    """The components it is about; () for the architecture; None when its scope is not modeled."""
    referencing = [
        n.id
        for n in topology.ir.nodes
        if n.kind not in OUT_OF_SCOPE and any(r.requirement_id == requirement.id for r in n.requirement_refs)
    ]
    if referencing:
        return tuple(sorted(referencing))
    scope = requirement.content.scope
    if scope is RequirementScope.SYSTEM:
        return ()
    kinds = SCOPE_KINDS.get(scope)
    if kinds is None:
        return None
    return tuple(n.id for n in topology.ir.nodes if n.kind in kinds)


def translate(
    requirements: tuple[Requirement, ...], topology: Topology
) -> tuple[tuple[Objective, ...], tuple[ObjectiveResult, ...]]:
    """The checkable objectives, and the verdicts of the requirements that cannot be checked."""
    objectives: list[Objective] = []
    verdicts: list[ObjectiveResult] = []
    for requirement in sorted(requirements, key=lambda r: r.number):
        content = requirement.content
        if not content.in_force or requirement.is_deleted or content.type not in TYPES:
            continue
        constraint = content.constraint
        if constraint is None:
            verdicts.append(
                _unsupported(requirement, "Stated in words only: not a machine-checkable objective.")
            )
            continue
        if not isinstance(constraint, QuantityConstraint) or constraint.percentile is not None:
            why = f"No reliability model checks a {constraint.metric} constraint of this form."
            verdicts.append(_unsupported(requirement, why))
            continue
        scope = _scope(requirement, topology)
        if scope is None:
            why = f"A {content.scope.value} scope is not modeled by the architecture."
            verdicts.append(_unsupported(requirement, why))
            continue
        if content.scope is not RequirementScope.SYSTEM and not scope:
            why = f"The architecture has no component of the {content.scope.value} scope."
            verdicts.append(_unsupported(requirement, why, Verdict.NOT_APPLICABLE))
            continue
        value, metric, operator = constraint.canonical_value, constraint.metric, constraint.operator
        name, rid = key(requirement), requirement.id
        if metric in {"availability", "uptime"} and operator in FLOORS:
            kind, strict = ObjectiveKind.AVAILABILITY, FLOORS[operator]
            objectives.append(Objective(name, kind, value, None, scope, rid, strict=strict))
        elif metric in TIMED and operator in CEILINGS:
            duration = Quantity.of(value, "ms")  # canonical milliseconds
            objectives.append(
                Objective(name, TIMED[metric], None, duration, scope, rid, strict=CEILINGS[operator])
            )
        else:
            verdicts.append(
                _unsupported(requirement, f"No reliability model checks {metric} {operator.value}.")
            )
    return tuple(objectives), tuple(verdicts)
