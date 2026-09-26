"""Architecture-level views of a cost result, without losing line-item traceability.

**Breakdowns** group the known monthly amounts by component, resource, category, provider, region,
fixed vs usage, and workload sensitivity. Every amount is in the result's one currency (another is
refused, never combined), and every view is monthly with hourly and annual views derived from it
(730 and 8760 hours): periods are converted, never added across. The groups of a breakdown add up
exactly to the known total.

**Unknown items** are listed on their own, with what each misses. The known total is a lower bound
whenever any item is unknown or unsupported (``complete`` is false): it is never presented as the
architecture's cost.

**Drivers** are explicit calculations over priced lines: the largest component and category, the
largest line items, the fixed and usage-based amounts, and how each line responds to workload:

- ``fixed``: declared resources; independent of the workload;
- ``stepwise``: instances the capacity analysis requires; they change in whole replicas;
- ``linear``: usage at a per-unit price; proportional to the workload;
- ``tiered``: usage at a graduated price; grows with the workload, at the rate of each tier.

A driver says how much and why, never whether a cost is justified: a large cost is not labelled
inefficient or wasteful.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from core.domain.requirements.value_objects import decimal_to_str

from .money import BillingPeriod, Money, arithmetic, convert, stored
from .pricing import PricingModel
from .results import CostCategory, CostKind, CostResult, CostStatus, LineItem, Share, Totals, breakdown

TOP_ITEMS = 10
STEPWISE_EVIDENCE = "declared_replicas"  # set only when the capacity analysis's replicas are billed


class Sensitivity(StrEnum):
    FIXED = "fixed"
    STEPWISE = "stepwise"
    LINEAR = "linear"
    TIERED = "tiered"


def sensitivity(line: LineItem) -> Sensitivity | None:
    """How a priced line responds to workload; None for an unknown line (its price is not known)."""
    if line.price is None:
        return None
    if line.kind is CostKind.USAGE:
        return Sensitivity.TIERED if line.price.model is PricingModel.TIERED else Sensitivity.LINEAR
    if any(e.label == STEPWISE_EVIDENCE for e in line.assumptions):
        return Sensitivity.STEPWISE
    return Sensitivity.FIXED


@dataclass(frozen=True, slots=True)
class DriverItem:
    element_id: str
    resource: str
    category: CostCategory
    kind: CostKind
    sensitivity: Sensitivity
    monthly: Money
    share: Decimal | None  # of the known monthly total

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "resource": self.resource,
            "category": self.category.value,
            "kind": self.kind.value,
            "sensitivity": self.sensitivity.value,
            "monthly": self.monthly.to_dict(),
            "annual": convert(self.monthly, BillingPeriod.MONTH, BillingPeriod.YEAR).to_dict(),
            "share": decimal_to_str(self.share) if self.share is not None else None,
        }


@dataclass(frozen=True, slots=True)
class UnknownItem:
    element_id: str
    resource: str
    category: CostCategory
    kind: CostKind
    missing: tuple[str, ...]
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "resource": self.resource,
            "category": self.category.value,
            "kind": self.kind.value,
            "missing": list(self.missing),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class CostDrivers:
    largest_component: Share | None
    largest_category: Share | None
    top_items: tuple[DriverItem, ...]
    fixed: Money  # declared resources, whatever the workload
    usage: Money  # priced by usage
    workload_sensitive: Money  # stepwise + linear + tiered: what changes when the workload does

    def to_dict(self) -> dict[str, Any]:
        return {
            "largest_component": self.largest_component.to_dict() if self.largest_component else None,
            "largest_category": self.largest_category.to_dict() if self.largest_category else None,
            "top_items": [item.to_dict() for item in self.top_items],
            "fixed": self.fixed.to_dict(),
            "usage": self.usage.to_dict(),
            "workload_sensitive": self.workload_sensitive.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CostSummary:
    status: CostStatus
    totals: Totals
    by_component: tuple[Share, ...]
    by_resource: tuple[Share, ...]
    by_category: tuple[Share, ...]
    by_provider: tuple[Share, ...]
    by_region: tuple[Share, ...]
    by_kind: tuple[Share, ...]
    by_sensitivity: tuple[Share, ...]
    unknown: tuple[UnknownItem, ...]
    drivers: CostDrivers

    @property
    def known_total_is_lower_bound(self) -> bool:
        return not self.totals.complete

    def to_dict(self) -> dict[str, Any]:
        def shares(values: tuple[Share, ...]) -> list[dict[str, Any]]:
            return [s.to_dict() for s in values]

        return {
            "status": self.status.value,
            "totals": self.totals.to_dict(),
            "known_total_is_lower_bound": self.known_total_is_lower_bound,
            "by_component": shares(self.by_component),
            "by_resource": shares(self.by_resource),
            "by_category": shares(self.by_category),
            "by_provider": shares(self.by_provider),
            "by_region": shares(self.by_region),
            "by_kind": shares(self.by_kind),
            "by_sensitivity": shares(self.by_sensitivity),
            "unknown": [u.to_dict() for u in self.unknown],
            "drivers": self.drivers.to_dict(),
        }


def _sum(amounts: list[Money], currency: str) -> Money:
    total = Money.zero(currency)
    for amount in amounts:
        total = total + amount
    return total


def summarize(result: CostResult, *, top: int = TOP_ITEMS) -> CostSummary:
    """Every breakdown and the drivers of ``result``; deterministic (ties broken by key)."""
    currency, lines, totals = result.currency, result.line_items, result.totals
    priced = [(line, line.monthly) for line in lines if line.monthly is not None]
    grand = totals.monthly.amount

    def share(amount: Money) -> Decimal | None:
        if not grand:
            return None
        with arithmetic():
            return stored(amount.amount / grand)

    ranked = sorted(priced, key=lambda pair: (-pair[1].amount, *pair[0].sort_key()))
    items = tuple(
        DriverItem(
            line.element_id,
            line.resource,
            line.category,
            line.kind,
            sensitivity(line) or Sensitivity.FIXED,  # a priced line always has one
            monthly,
            share(monthly),
        )
        for line, monthly in ranked[:top]
    )
    by_component, by_category = result.by_component(), result.by_category()
    known_components = [s for s in by_component if s.priced_items]
    known_categories = [s for s in by_category if s.priced_items]
    drivers = CostDrivers(
        known_components[0] if known_components else None,
        known_categories[0] if known_categories else None,
        items,
        _sum([m for line, m in priced if line.kind is CostKind.FIXED], currency),
        _sum([m for line, m in priced if line.kind is CostKind.USAGE], currency),
        _sum([m for line, m in priced if sensitivity(line) is not Sensitivity.FIXED], currency),
    )
    unknown = tuple(
        UnknownItem(line.element_id, line.resource, line.category, line.kind, line.missing, line.reason)
        for line in lines
        if line.monthly is None
    )
    return CostSummary(
        result.status,
        totals,
        by_component,
        result.by_resource(),
        by_category,
        result.by_provider(),
        result.by_region(),
        result.by_kind(),
        breakdown(lines, lambda line: s.value if (s := sensitivity(line)) else None, currency),
        unknown,
        drivers,
    )
