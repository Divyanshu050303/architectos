"""A cost analysis: one execution of the cost models against one architecture revision, one
pricing snapshot and, optionally, one capacity analysis of the same revision.

The **request** names the exact revision, the pricing snapshot (whose prices are the only ones
used), the currency every amount is in (no conversion), the date prices must be effective on, the
operating hours per month (default 730: resources run all month, a documented convention), the
capacity analysis whose usage quantities feed usage-based prices, and analysis-level assumptions.

Lifecycle: ``pending`` → ``running`` → what the result established (``completed``, ``partial``,
``insufficient_pricing``, ``unsupported``) or ``failed`` (no result, a safe error). Final states are
final.
"""

import re
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from core.domain.engine_results import Evidence
from core.domain.requirements.value_objects import decimal_to_str

from .errors import InvalidCostAnalysisTransition, InvalidCostRequest, InvalidMoney
from .money import HOURS_PER_MONTH, amount, currency
from .results import CostResult, CostStatus, Totals

MAX_ASSUMPTIONS = 50
MAX_LABEL_LENGTH = 100
ASSUMPTION_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PENDING, RUNNING = "pending", "running"


def _invalid(field: str, reason: str) -> InvalidCostRequest:
    return InvalidCostRequest(details={"field": field, "reason": reason})


@dataclass(frozen=True, slots=True)
class CostAssumption:
    key: str
    statement: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not ASSUMPTION_KEY.fullmatch(self.key):
            raise _invalid("assumptions.key", "invalid_key")
        if not isinstance(self.statement, str) or not self.statement.strip() or len(self.statement) > 500:
            raise _invalid("assumptions.statement", "invalid_text")

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "statement": self.statement}


@dataclass(frozen=True, slots=True)
class CostAnalysisRequest:
    architecture_id: uuid.UUID
    revision_number: int
    snapshot_id: uuid.UUID
    currency: str
    pricing_date: date  # prices must be effective on this day
    operating_hours_per_month: Decimal = HOURS_PER_MONTH  # 730: all month
    capacity_analysis_id: uuid.UUID | None = None
    assumptions: tuple[CostAssumption, ...] = ()
    label: str | None = None

    def __post_init__(self) -> None:
        for field in ("architecture_id", "snapshot_id"):
            if not isinstance(getattr(self, field), uuid.UUID):
                raise _invalid(field, "invalid_reference")
        if self.capacity_analysis_id is not None and not isinstance(self.capacity_analysis_id, uuid.UUID):
            raise _invalid("capacity_analysis_id", "invalid_reference")
        if (
            isinstance(self.revision_number, bool)
            or not isinstance(self.revision_number, int)
            or self.revision_number < 1
        ):
            raise _invalid("revision_number", "not_a_positive_count")
        try:
            currency(self.currency)
            hours = amount(self.operating_hours_per_month, "operating_hours_per_month")
        except InvalidMoney as error:
            raise _invalid(error.details["field"], error.details["reason"]) from None
        if not 0 < hours <= HOURS_PER_MONTH:
            raise _invalid("operating_hours_per_month", "out_of_range")
        object.__setattr__(self, "operating_hours_per_month", hours)
        if not isinstance(self.pricing_date, date) or isinstance(self.pricing_date, datetime):
            raise _invalid("pricing_date", "not_a_date")
        if not isinstance(self.assumptions, tuple) or len(self.assumptions) > MAX_ASSUMPTIONS:
            raise _invalid("assumptions", "too_many")
        keys = [a.key for a in self.assumptions]
        if len(keys) != len(set(keys)):
            raise _invalid("assumptions", "duplicate_key")
        object.__setattr__(self, "assumptions", tuple(sorted(self.assumptions, key=lambda a: a.key)))
        if self.label is not None and (
            not isinstance(self.label, str) or not self.label.strip() or len(self.label) > MAX_LABEL_LENGTH
        ):
            raise _invalid("label", "invalid_text")

    @property
    def uptime(self) -> Decimal:
        """The share of the month resources run: operating hours / 730."""
        return self.operating_hours_per_month / HOURS_PER_MONTH

    def billing_assumptions(self) -> tuple[Evidence, ...]:
        return (
            Evidence("hours_per_month", decimal_to_str(HOURS_PER_MONTH)),
            Evidence("operating_hours_per_month", decimal_to_str(self.operating_hours_per_month)),
            Evidence("pricing_date", self.pricing_date.isoformat()),
            Evidence("currency", self.currency),
        )

    def inputs(self) -> dict[str, Any]:
        """Everything the result depends on besides the revision and the snapshot's content."""
        return {
            "architecture_id": str(self.architecture_id),
            "revision_number": self.revision_number,
            "snapshot_id": str(self.snapshot_id),
            "currency": self.currency,
            "pricing_date": self.pricing_date.isoformat(),
            "operating_hours_per_month": decimal_to_str(self.operating_hours_per_month),
            "capacity_analysis_id": str(self.capacity_analysis_id) if self.capacity_analysis_id else None,
            "assumptions": [a.to_dict() for a in self.assumptions],
        }


_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({RUNNING, CostStatus.FAILED.value}),
    RUNNING: frozenset(s.value for s in CostStatus),
    **{s.value: frozenset() for s in CostStatus},
}


@dataclass(frozen=True, slots=True)
class CostAnalysisError:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class CostAnalysis:
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
    result: CostResult | None = None
    error: CostAnalysisError | None = None

    @property
    def totals(self) -> Totals | None:
        return self.result.totals if self.result is not None else None

    def _move(self, to: str) -> None:
        if to not in _TRANSITIONS[self.status]:
            raise InvalidCostAnalysisTransition(details={"from": self.status, "to": to})

    def start(self, at: datetime) -> CostAnalysis:
        self._move(RUNNING)
        return replace(self, status=RUNNING, started_at=at)

    def finish(self, result: CostResult, at: datetime) -> CostAnalysis:
        self._move(result.status.value)
        return replace(self, status=result.status.value, completed_at=at, result=result)

    def fail(self, error: CostAnalysisError, at: datetime) -> CostAnalysis:
        self._move(CostStatus.FAILED.value)
        return replace(self, status=CostStatus.FAILED.value, completed_at=at, error=error)
