"""A simulation: one scenario evaluated against one exact architecture revision, next to the same
revision without it (the baseline), by the engines that support it.

The **request** names the exact revision, the scenario (stored as an immutable snapshot, never the
architecture itself), which analyses to run (default: every one whose inputs are given), the inputs
those engines need — the Capacity Engine's workload profile, the entry points, the Cost Engine's
pricing snapshot, date and operating hours — and assumptions (recorded, never computed with). An
analysis whose inputs are missing is reported unsupported, never run on invented ones.

Lifecycle: ``pending`` → ``running`` → a final status (what the result established, or ``failed``).
Final statuses are final. Simulations reference the revision (number and content hash); they never
copy it.
"""

import re
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from core.domain.capacity.workload import WorkloadProfile
from core.domain.cost.analyses import CostAnalysisRequest
from core.domain.cost.errors import InvalidCostRequest
from core.domain.cost.money import HOURS_PER_MONTH
from core.domain.requirements.value_objects import decimal_to_str

from .errors import InvalidSimulationRequest, InvalidSimulationTransition
from .results import SimulationResult
from .scenarios import MAX_ELEMENT_ID, Scenario
from .values import AnalysisKind, SimulationStatus

KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
MAX_ASSUMPTIONS = 50
MAX_ENTRIES = 50
MAX_LABEL_LENGTH = 100
PENDING, RUNNING = "pending", "running"
_ANY_ARCHITECTURE = uuid.UUID(int=0)  # to check pricing inputs with the Cost Engine's own rules


def _invalid(field: str, reason: str) -> InvalidSimulationRequest:
    return InvalidSimulationRequest(details={"field": field, "reason": reason})


@dataclass(frozen=True, slots=True)
class SimulationAssumption:
    """Something the simulation takes as true, stated by a person; recorded, never computed with."""

    key: str
    statement: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not KEY.fullmatch(self.key):
            raise _invalid("assumptions.key", "invalid_key")
        if not isinstance(self.statement, str) or not self.statement.strip() or len(self.statement) > 500:
            raise _invalid("assumptions.statement", "invalid_text")

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "statement": self.statement}


@dataclass(frozen=True, slots=True)
class PricingInputs:
    """What the Cost Engine needs to price the baseline and the scenario: one snapshot for both."""

    snapshot_id: uuid.UUID
    pricing_date: date
    operating_hours_per_month: Decimal = HOURS_PER_MONTH

    def __post_init__(self) -> None:
        checked = self.cost_request(_ANY_ARCHITECTURE, 1, "USD")  # the currency comes from the snapshot
        object.__setattr__(self, "operating_hours_per_month", checked.operating_hours_per_month)

    def cost_request(self, architecture_id: uuid.UUID, revision: int, currency: str) -> CostAnalysisRequest:
        """These inputs as the Cost Engine's request (validated by its rules, not a copy of them)."""
        try:
            return CostAnalysisRequest(
                architecture_id=architecture_id,
                revision_number=revision,
                snapshot_id=self.snapshot_id,
                currency=currency,
                pricing_date=self.pricing_date,
                operating_hours_per_month=self.operating_hours_per_month,
            )
        except InvalidCostRequest as error:
            raise _invalid(f"pricing.{error.details['field']}", error.details["reason"]) from None

    def to_dict(self) -> dict[str, str]:
        return {
            "snapshot_id": str(self.snapshot_id),
            "pricing_date": self.pricing_date.isoformat(),
            "operating_hours_per_month": decimal_to_str(self.operating_hours_per_month),
        }


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    architecture_id: uuid.UUID
    revision_number: int
    scenario: Scenario
    analyses: tuple[AnalysisKind, ...] | None = None  # None: every analysis whose inputs are given
    workload: WorkloadProfile | None = None  # the Capacity Engine's (and the Cost Engine's usage)
    entries: tuple[str, ...] | None = None  # where requests enter; None: the clients
    pricing: PricingInputs | None = None
    assumptions: tuple[SimulationAssumption, ...] = ()
    label: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.architecture_id, uuid.UUID):
            raise _invalid("architecture_id", "invalid_reference")
        if (
            isinstance(self.revision_number, bool)
            or not isinstance(self.revision_number, int)
            or self.revision_number < 1
        ):
            raise _invalid("revision_number", "not_a_positive_count")
        if not isinstance(self.scenario, Scenario):
            raise _invalid("scenario", "required")
        self._analyses()
        if self.workload is not None and not isinstance(self.workload, WorkloadProfile):
            raise _invalid("workload", "not_an_object")
        if self.pricing is not None and not isinstance(self.pricing, PricingInputs):
            raise _invalid("pricing", "not_an_object")
        self._entries()
        self._assumptions()
        if self.label is not None and (
            not isinstance(self.label, str) or not self.label.strip() or len(self.label) > MAX_LABEL_LENGTH
        ):
            raise _invalid("label", "invalid_text")

    def _analyses(self) -> None:
        analyses = self.analyses
        if analyses is None:
            return
        if not isinstance(analyses, tuple) or not analyses:
            raise _invalid("analyses", "empty")
        if not all(isinstance(a, AnalysisKind) for a in analyses):
            raise _invalid("analyses", "unknown_analysis")
        object.__setattr__(self, "analyses", tuple(sorted(set(analyses), key=lambda a: a.value)))

    def _entries(self) -> None:
        entries = self.entries
        if entries is None:
            return
        if not isinstance(entries, tuple) or not 0 < len(entries) <= MAX_ENTRIES:
            raise _invalid("entries", "invalid_entries")
        if not all(isinstance(e, str) and 0 < len(e) <= MAX_ELEMENT_ID for e in entries):
            raise _invalid("entries", "invalid_entries")
        object.__setattr__(self, "entries", tuple(sorted(set(entries))))

    def _assumptions(self) -> None:
        assumptions = self.assumptions
        if not isinstance(assumptions, tuple) or len(assumptions) > MAX_ASSUMPTIONS:
            raise _invalid("assumptions", "too_many")
        if not all(isinstance(a, SimulationAssumption) for a in assumptions):
            raise _invalid("assumptions", "invalid_assumption")
        keys = [a.key for a in assumptions]
        if len(keys) != len(set(keys)):
            raise _invalid("assumptions", "duplicate_key")
        object.__setattr__(self, "assumptions", tuple(sorted(assumptions, key=lambda a: a.key)))

    def inputs(self) -> dict[str, Any]:
        """What the request contributes to the result, canonically."""
        return {
            "architecture_id": str(self.architecture_id),
            "revision_number": self.revision_number,
            "scenario": self.scenario.to_dict(),
            "analyses": [a.value for a in self.analyses] if self.analyses is not None else None,
            "workload": self.workload.to_dict() if self.workload is not None else None,
            "entries": list(self.entries) if self.entries is not None else None,
            "pricing": self.pricing.to_dict() if self.pricing is not None else None,
            "assumptions": [a.to_dict() for a in self.assumptions],
        }


_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({RUNNING, SimulationStatus.FAILED.value}),
    RUNNING: frozenset(s.value for s in SimulationStatus),
    **{s.value: frozenset() for s in SimulationStatus},
}


@dataclass(frozen=True, slots=True)
class SimulationError:
    """Why a simulation failed: a stable code and a sentence safe to show (never internals)."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Simulation:
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    revision_number: int
    revision_content_hash: str
    status: str
    requested_by_user_id: uuid.UUID | None
    requested_at: datetime
    label: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: SimulationResult | None = None
    error: SimulationError | None = None

    @property
    def finished(self) -> bool:
        return self.status not in (PENDING, RUNNING)

    def _move(self, to: str) -> None:
        if to not in _TRANSITIONS[self.status]:
            raise InvalidSimulationTransition(details={"from": self.status, "to": to})

    def start(self, at: datetime) -> Simulation:
        self._move(RUNNING)
        return replace(self, status=RUNNING, started_at=at)

    def finish(self, result: SimulationResult, at: datetime) -> Simulation:
        self._move(result.status.value)
        return replace(self, status=result.status.value, completed_at=at, result=result)

    def fail(self, error: SimulationError, at: datetime) -> Simulation:
        self._move(SimulationStatus.FAILED.value)
        return replace(self, status=SimulationStatus.FAILED.value, completed_at=at, error=error)
