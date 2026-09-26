"""Cost projections: billing periods under explicit conventions, and scenarios compared with the
baseline.

**Periods.** A cost result is monthly. Other periods are derived from it by hours, under fixed
conventions (``CONVENTIONS``): a month is 730 hours (8760 / 12: every month is the same length by
convention, about 30.42 days; calendar months are not modelled), a day 24 hours, a year 8760 hours
(365 days). Resources run for the request's operating hours per month (``uptime`` = operating
hours / 730); usage accrues over the same hours.

**Scenarios.** A cost scenario is a capacity scenario (a workload multiplier, compound growth, or a
target rate, and declared configuration changes such as replicas or storage) with, optionally,
other operating hours or billing the replicas the capacity analysis requires. It is priced with the
**same pricing snapshot** as the baseline, and its usage comes from the capacity engine's run of the
scenario: costs are recalculated, never extrapolated linearly, so tiered prices and replica steps
come out as they are.

**Comparison.** Baseline and scenario known totals, their difference, and the percentage change
only when it is defined (both complete and a baseline above 0), each changed line (quantity,
amount, status), the changed assumptions, and the lines whose cost is unknown on either side
(always listed: their difference cannot be known).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from core.domain.capacity.scenarios import Scenario
from core.domain.engine_results import Evidence
from core.domain.numbers import arithmetic
from core.domain.requirements.value_objects import decimal_to_str

from .aggregation import Sensitivity, sensitivity
from .analyses import CostAnalysisRequest
from .errors import InvalidCostRequest, InvalidMoney
from .money import HOURS_PER_MONTH, BillingPeriod, Money, amount, convert, stored
from .results import CostResult, LineItem, LineStatus

MAX_COST_SCENARIOS = 10
CONVENTIONS: dict[str, str] = {
    "hours_per_day": "24",
    "hours_per_month": "730",
    "hours_per_year": "8760",
    "days_per_month": "30.416666666667",  # 730 / 24, every month alike by convention
    "days_per_year": "365",
}


def _invalid(field: str, reason: str) -> InvalidCostRequest:
    return InvalidCostRequest(details={"field": field, "reason": reason})


@dataclass(frozen=True, slots=True)
class PeriodAmounts:
    """One recurring amount per billing period, all derived from the monthly amount."""

    monthly: Money

    def per(self, period: BillingPeriod) -> Money:
        return convert(self.monthly, BillingPeriod.MONTH, period)

    def to_dict(self) -> dict[str, Any]:
        return {period.value: self.per(period).to_dict() for period in BillingPeriod}


def assumptions(
    request: CostAnalysisRequest, result: CostResult, workload: Evidence | None = None
) -> tuple[Evidence, ...]:
    """What a projection rests on, stated: periods, operating hours, uptime, snapshot, currency,
    pricing date, the workload (when a capacity analysis supplies usage) and the request's own."""
    with arithmetic():
        uptime = stored(request.operating_hours_per_month / HOURS_PER_MONTH)
    stated = [
        *(Evidence(label, value) for label, value in CONVENTIONS.items()),
        Evidence("operating_hours_per_month", decimal_to_str(request.operating_hours_per_month)),
        Evidence("uptime", decimal_to_str(uptime)),
        Evidence("pricing_snapshot", f"{result.snapshot_id} ({result.snapshot_hash})"),
        Evidence("currency", result.currency),
        Evidence("pricing_date", request.pricing_date.isoformat()),
        Evidence("replicas_from_capacity", "true" if request.replicas_from_capacity else "false"),
        *(Evidence(f"assumption.{a.key}", a.statement) for a in request.assumptions),
    ]
    if workload is not None:
        stated.append(workload)
    return tuple(stated)


@dataclass(frozen=True, slots=True)
class CostScenario:
    """A capacity scenario, priced; optionally other operating hours or capacity-required replicas."""

    scenario: Scenario
    operating_hours_per_month: Decimal | None = None
    replicas_from_capacity: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario, Scenario):
            raise _invalid("scenario", "required")
        if self.operating_hours_per_month is not None:
            try:
                hours = amount(self.operating_hours_per_month, "operating_hours_per_month")
            except InvalidMoney as error:
                raise _invalid("operating_hours_per_month", error.details["reason"]) from None
            if not 0 < hours <= HOURS_PER_MONTH:
                raise _invalid("operating_hours_per_month", "out_of_range")
            object.__setattr__(self, "operating_hours_per_month", hours)
        if self.replicas_from_capacity is not None and not isinstance(self.replicas_from_capacity, bool):
            raise _invalid("replicas_from_capacity", "not_a_boolean")

    @property
    def name(self) -> str:
        return self.scenario.name

    @property
    def changes_workload(self) -> bool:
        return self.scenario.multiplier is not None or self.scenario.target_rate is not None

    def changed_assumptions(self, baseline: CostAnalysisRequest) -> tuple[Evidence, ...]:
        found: list[Evidence] = []
        if (multiplier := self.scenario.multiplier) is not None:
            found.append(Evidence("workload_multiplier", decimal_to_str(multiplier)))
        if self.scenario.target_rate is not None:
            rate = self.scenario.target_rate
            found.append(Evidence("workload_target_rate", f"{decimal_to_str(rate.value)} {rate.unit}"))
        if self.scenario.target_utilization is not None:
            found.append(Evidence("target_utilization", decimal_to_str(self.scenario.target_utilization)))
        found += [
            Evidence(f"{c.element_id}.{c.property}", decimal_to_str(c.value)) for c in self.scenario.changes
        ]
        hours = self.operating_hours_per_month
        if hours is not None and hours != baseline.operating_hours_per_month:
            found.append(Evidence("operating_hours_per_month", decimal_to_str(hours)))
        replicas = self.replicas_from_capacity
        if replicas is not None and replicas != baseline.replicas_from_capacity:
            found.append(Evidence("replicas_from_capacity", "true" if replicas else "false"))
        return tuple(found)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.to_dict(),
            "operating_hours_per_month": decimal_to_str(self.operating_hours_per_month)
            if self.operating_hours_per_month is not None
            else None,
            "replicas_from_capacity": self.replicas_from_capacity,
        }


@dataclass(frozen=True, slots=True)
class LineDelta:
    """One line that differs between baseline and scenario (quantity, amount or status); a side
    without the line is None."""

    element_id: str
    resource: str
    baseline_quantity: Decimal | None
    scenario_quantity: Decimal | None
    baseline_monthly: Money | None
    scenario_monthly: Money | None
    baseline_status: LineStatus | None
    scenario_status: LineStatus | None
    sensitivity: Sensitivity | None  # of the scenario's line (else the baseline's)

    @property
    def difference(self) -> Money | None:
        """Scenario - baseline, only when both amounts are known."""
        if self.baseline_monthly is None or self.scenario_monthly is None:
            return None
        return self.scenario_monthly - self.baseline_monthly

    @property
    def unknown(self) -> bool:
        return self.baseline_monthly is None or self.scenario_monthly is None

    def to_dict(self) -> dict[str, Any]:
        def number(value: Decimal | None) -> str | None:
            return decimal_to_str(value) if value is not None else None

        def money(value: Money | None) -> dict[str, str] | None:
            return value.to_dict() if value is not None else None

        return {
            "element_id": self.element_id,
            "resource": self.resource,
            "baseline_quantity": number(self.baseline_quantity),
            "scenario_quantity": number(self.scenario_quantity),
            "baseline_monthly": money(self.baseline_monthly),
            "scenario_monthly": money(self.scenario_monthly),
            "difference": money(self.difference),
            "baseline_status": self.baseline_status.value if self.baseline_status else None,
            "scenario_status": self.scenario_status.value if self.scenario_status else None,
            "sensitivity": self.sensitivity.value if self.sensitivity else None,
        }


def _deltas(baseline: CostResult, scenario: CostResult) -> tuple[LineDelta, ...]:
    before = {(line.element_id, line.resource): line for line in baseline.line_items}
    after = {(line.element_id, line.resource): line for line in scenario.line_items}
    found = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        same = (  # an unknown line is always listed: its difference cannot be known
            old is not None
            and new is not None
            and old.priced
            and new.priced
            and (old.quantity, old.monthly) == (new.quantity, new.monthly)
        )
        if same:
            continue

        def get(line: LineItem | None, name: str) -> Any:
            return getattr(line, name) if line is not None else None

        found.append(
            LineDelta(
                key[0],
                key[1],
                get(old, "quantity"),
                get(new, "quantity"),
                get(old, "monthly"),
                get(new, "monthly"),
                get(old, "status"),
                get(new, "status"),
                sensitivity(new) if new is not None else sensitivity(old) if old is not None else None,
            )
        )
    return tuple(found)


@dataclass(frozen=True, slots=True)
class CostComparison:
    baseline: Money  # known monthly totals
    scenario: Money
    baseline_complete: bool
    scenario_complete: bool
    changed_lines: tuple[LineDelta, ...]
    changed_assumptions: tuple[Evidence, ...]

    @classmethod
    def between(
        cls, baseline: CostResult, scenario: CostResult, changed: tuple[Evidence, ...]
    ) -> CostComparison:
        if baseline.currency != scenario.currency:  # the same snapshot and request: a programming error
            raise _invalid("currency", "currency_mismatch")
        before, after = baseline.totals, scenario.totals
        return cls(
            before.monthly,
            after.monthly,
            before.complete,
            after.complete,
            _deltas(baseline, scenario),
            changed,
        )

    @property
    def complete(self) -> bool:
        return self.baseline_complete and self.scenario_complete

    @property
    def difference(self) -> Money:
        """Of the known totals; the true difference when ``complete``, else between lower bounds."""
        return self.scenario - self.baseline

    @property
    def percentage(self) -> Decimal | None:
        """(scenario - baseline) / baseline x 100, only when both are complete and baseline > 0."""
        if not self.complete or self.baseline.amount == 0:
            return None
        with arithmetic():
            return stored(self.difference.amount / self.baseline.amount * 100)

    @property
    def percentage_undefined(self) -> str | None:
        if not self.complete:
            return "incomplete"
        return "zero_baseline" if self.baseline.amount == 0 else None

    @property
    def unknown_differences(self) -> tuple[LineDelta, ...]:
        return tuple(d for d in self.changed_lines if d.unknown)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": PeriodAmounts(self.baseline).to_dict(),
            "scenario": PeriodAmounts(self.scenario).to_dict(),
            "baseline_complete": self.baseline_complete,
            "scenario_complete": self.scenario_complete,
            "complete": self.complete,
            "difference": PeriodAmounts(self.difference).to_dict(),
            "percentage": decimal_to_str(self.percentage) if self.percentage is not None else None,
            "percentage_undefined": self.percentage_undefined,
            "changed_lines": [d.to_dict() for d in self.changed_lines],
            "changed_assumptions": [e.to_dict() for e in self.changed_assumptions],
            "unknown_differences": [f"{d.element_id}/{d.resource}" for d in self.unknown_differences],
        }


@dataclass(frozen=True, slots=True)
class ScenarioProjection:
    scenario: CostScenario
    result: CostResult
    comparison: CostComparison
    assumptions: tuple[Evidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.scenario.name,
            "scenario": self.scenario.to_dict(),
            "status": self.result.status.value,
            "periods": PeriodAmounts(self.result.totals.monthly).to_dict(),
            "comparison": self.comparison.to_dict(),
            "assumptions": [e.to_dict() for e in self.assumptions],
            "result_fingerprint": self.result.fingerprint,
        }
