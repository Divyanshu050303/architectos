"""What an evolution analysis is asked to achieve: typed goals, each evaluated by an existing engine.

A goal states its target explicitly, with its unit — never inferred from an industry benchmark:

- ``increase_workload``: a rate to support (``target``: a request, operation or event rate).
- ``cost_ceiling``: a monthly amount not to exceed (``amount`` in ``currency``).
- ``availability_objective``: an availability to reach (``target``: a ratio or %, below 1).
- ``recovery_objective``: a recovery time not to exceed (``target``: a duration).
- ``address_finding``: a finding to resolve (``finding``: the engine and the finding's stable id).
- ``observability_coverage``: a dimension required where it matters (``dimension``).
- ``satisfy_requirement``: an in-force requirement of the project (``requirement_id``).

A goal carries its provenance (``user``, or ``requirement`` with the requirement it was derived
from) and, optionally, a priority on the requirements' scale. Fields a type does not use must be
absent, so equal goals are equal and a goal's ``key`` identifies it.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Self

from core.domain.capacity.errors import InvalidQuantity
from core.domain.capacity.units import Dimension, Quantity
from core.domain.cost.errors import InvalidMoney
from core.domain.cost.money import currency as checked_currency
from core.domain.observability.values import Dimension as Coverage
from core.domain.requirements.enums import RequirementPriority
from core.domain.requirements.value_objects import decimal_to_str

from .errors import InvalidEvolutionRequest
from .values import FINDING_SOURCES, EvidenceSource, GoalSource, GoalType

MAX_FINDING_ID = 128
RATES = (Dimension.REQUEST_RATE, Dimension.OPERATION_RATE, Dimension.EVENT_RATE)
_METHODS = {
    GoalType.INCREASE_WORKLOAD: EvidenceSource.CAPACITY,
    GoalType.COST_CEILING: EvidenceSource.COST,
    GoalType.AVAILABILITY_OBJECTIVE: EvidenceSource.RELIABILITY,
    GoalType.RECOVERY_OBJECTIVE: EvidenceSource.RELIABILITY,
    GoalType.OBSERVABILITY_COVERAGE: EvidenceSource.OBSERVABILITY,
    GoalType.SATISFY_REQUIREMENT: EvidenceSource.REQUIREMENT,  # resolved by the requirement's metric
}
_FIELDS = {  # the fields each type requires; every other optional field must be absent
    GoalType.INCREASE_WORKLOAD: {"target"},
    GoalType.COST_CEILING: {"amount", "currency"},
    GoalType.AVAILABILITY_OBJECTIVE: {"target"},
    GoalType.RECOVERY_OBJECTIVE: {"target"},
    GoalType.ADDRESS_FINDING: {"finding"},
    GoalType.OBSERVABILITY_COVERAGE: {"dimension"},
    GoalType.SATISFY_REQUIREMENT: {"requirement_id"},
}
_OPTIONAL = ("target", "amount", "currency", "finding", "dimension", "requirement_id")


def _invalid(field: str, reason: str, **details: str) -> InvalidEvolutionRequest:
    return InvalidEvolutionRequest(details={"field": f"goals.{field}", "reason": reason, **details})


@dataclass(frozen=True, slots=True)
class FindingRef:
    """A finding of an engine, by the stable id that engine gives it."""

    source: EvidenceSource
    finding_id: str

    def __post_init__(self) -> None:
        if self.source not in FINDING_SOURCES:
            raise _invalid("finding.source", "not_a_finding_source")
        if not isinstance(self.finding_id, str) or not 0 < len(self.finding_id) <= MAX_FINDING_ID:
            raise _invalid("finding.finding_id", "invalid_reference")

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source.value, "finding_id": self.finding_id}


@dataclass(frozen=True, slots=True)
class EvolutionGoal:
    type: GoalType
    target: Quantity | None = None
    amount: Decimal | None = None
    currency: str | None = None
    finding: FindingRef | None = None
    dimension: Coverage | None = None
    requirement_id: uuid.UUID | None = None
    priority: RequirementPriority | None = None
    source: GoalSource = GoalSource.USER
    derived_from: uuid.UUID | None = None  # the requirement a derived goal comes from

    def __post_init__(self) -> None:
        if not isinstance(self.type, GoalType):
            raise _invalid("type", "unknown_goal_type")
        self._fields()
        self._target()
        if self.amount is not None:
            if not isinstance(self.amount, Decimal) or not self.amount.is_finite() or self.amount < 0:
                raise _invalid("amount", "not_a_non_negative_amount")
            object.__setattr__(self, "amount", self.amount.normalize() + 0)
        if self.currency is not None:
            try:
                checked_currency(self.currency)
            except InvalidMoney:
                raise _invalid("currency", "invalid_currency") from None
        if self.finding is not None and not isinstance(self.finding, FindingRef):
            raise _invalid("finding", "not_an_object")
        if self.dimension is not None and not isinstance(self.dimension, Coverage):
            raise _invalid("dimension", "unknown_dimension")
        if self.requirement_id is not None and not isinstance(self.requirement_id, uuid.UUID):
            raise _invalid("requirement_id", "invalid_reference")
        if self.priority is not None and not isinstance(self.priority, RequirementPriority):
            raise _invalid("priority", "unknown_priority")
        if not isinstance(self.source, GoalSource):
            raise _invalid("source", "unknown_source")
        derived = self.source is GoalSource.REQUIREMENT
        if derived != isinstance(self.derived_from, uuid.UUID) or (
            not derived and self.derived_from is not None
        ):
            raise _invalid("derived_from", "required" if derived else "not_applicable")

    def _fields(self) -> None:
        required = _FIELDS[self.type]
        for name in _OPTIONAL:
            if (getattr(self, name) is not None) != (name in required):
                reason = "required" if name in required else "not_applicable"
                raise _invalid(name, reason, type=self.type.value)

    def _target(self) -> None:
        target = self.target
        if target is None:
            return
        if not isinstance(target, Quantity):
            raise _invalid("target", "not_a_quantity")
        try:
            match self.type:
                case GoalType.INCREASE_WORKLOAD:
                    target.require(*RATES, field="goals.target")
                    positive = target.value > 0
                case GoalType.RECOVERY_OBJECTIVE:
                    target.require(Dimension.DURATION, field="goals.target")
                    positive = target.value > 0
                case _:  # availability
                    target.require(Dimension.RATIO, field="goals.target")
                    positive = 0 < target.canonical < 1
        except InvalidQuantity as error:
            raise _invalid("target", str(error.details.get("reason") or "invalid_unit")) from None
        if not positive:
            raise _invalid("target", "out_of_range", type=self.type.value)

    @property
    def method(self) -> EvidenceSource:
        """The engine that evaluates this goal."""
        if self.type is GoalType.ADDRESS_FINDING:
            assert self.finding is not None  # noqa: S101 -- required by the type
            return self.finding.source
        return _METHODS[self.type]

    @property
    def key(self) -> str:
        """Identifies the goal: a request never states the same goal twice; ordering follows it."""
        match self.type:
            case GoalType.INCREASE_WORKLOAD | GoalType.RECOVERY_OBJECTIVE | GoalType.AVAILABILITY_OBJECTIVE:
                assert self.target is not None  # noqa: S101 -- required by the type
                canonical = self.target.canonical_quantity()
                detail = f"{decimal_to_str(canonical.value)} {canonical.unit}"
            case GoalType.COST_CEILING:
                assert self.amount is not None  # noqa: S101 -- required by the type
                detail = f"{decimal_to_str(self.amount)} {self.currency}/month"
            case GoalType.ADDRESS_FINDING:
                assert self.finding is not None  # noqa: S101 -- required by the type
                detail = f"{self.finding.source.value}:{self.finding.finding_id}"
            case GoalType.OBSERVABILITY_COVERAGE:
                assert self.dimension is not None  # noqa: S101 -- required by the type
                detail = self.dimension.value
            case _:
                detail = str(self.requirement_id)
        return f"{self.type.value}:{detail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.type.value,
            "target": self.target.to_dict() if self.target is not None else None,
            "amount": decimal_to_str(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "finding": self.finding.to_dict() if self.finding is not None else None,
            "dimension": self.dimension.value if self.dimension is not None else None,
            "requirement_id": str(self.requirement_id) if self.requirement_id is not None else None,
            "priority": self.priority.value if self.priority is not None else None,
            "source": self.source.value,
            "derived_from": str(self.derived_from) if self.derived_from is not None else None,
            "method": self.method.value,
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        """A goal from its serialized form (a request or a stored analysis): every field validated."""
        if not isinstance(data, Mapping):
            raise _invalid("", "not_an_object")
        try:
            finding = data.get("finding")
            return cls(
                type=GoalType(data["type"]),
                target=Quantity.from_dict(data["target"], "goals.target") if data.get("target") else None,
                amount=Decimal(str(data["amount"])) if data.get("amount") is not None else None,
                currency=data.get("currency"),
                finding=(
                    FindingRef(EvidenceSource(finding["source"]), finding["finding_id"]) if finding else None
                ),
                dimension=Coverage(data["dimension"]) if data.get("dimension") else None,
                requirement_id=uuid.UUID(data["requirement_id"]) if data.get("requirement_id") else None,
                priority=RequirementPriority(data["priority"]) if data.get("priority") else None,
                source=GoalSource(data.get("source", GoalSource.USER.value)),
                derived_from=uuid.UUID(data["derived_from"]) if data.get("derived_from") else None,
            )
        except InvalidQuantity as error:
            raise _invalid("target", str(error.details.get("reason") or "invalid_unit")) from None
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            raise _invalid("", "malformed", error=type(error).__name__) from None
