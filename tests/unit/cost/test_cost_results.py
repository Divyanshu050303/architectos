"""Cost line items, results and the analysis lifecycle (Milestone 8, phase 1)."""

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.domain.cost.analyses import CostAnalysis, CostAnalysisError, CostAnalysisRequest, CostAssumption
from core.domain.cost.errors import InvalidCostAnalysisTransition, InvalidCostRequest, InvalidCostResult
from core.domain.cost.money import Money
from core.domain.cost.pricing import PricingModel, PricingSource, PricingUnit
from core.domain.cost.results import (
    CostCategory,
    CostKind,
    CostResult,
    CostStatus,
    LineItem,
    LineStatus,
    PriceRef,
)
from core.domain.engine_results import Evidence, Limitation, ModelSet, Unsupported

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
SNAPSHOT = uuid.UUID(int=7)
MODELS = ModelSet.of([("instance-hours", 1)])


def price(currency: str = "USD") -> PriceRef:
    return PriceRef(
        SNAPSHOT,
        "rds-r6g-large",
        "aws",
        "rds",
        "db.r6g.large",
        "eu-west-1",
        currency,
        PricingUnit.INSTANCE_HOUR,
        PricingModel.PER_UNIT,
        date(2026, 9, 1),
        None,
        PricingSource.USER_INPUT,
        NOW,
    )


def priced(
    node: str = "db", resource: str = "instance_hours", amount: str = "189.8", **kwargs: Any
) -> LineItem:
    fields: dict[str, Any] = {
        "category": CostCategory.DATABASE,
        "kind": CostKind.FIXED,
        "status": LineStatus.PRICED,
        "quantity": Decimal(730),
        "unit": PricingUnit.INSTANCE_HOUR,
        "unit_price": Decimal("0.26"),
        "per": Decimal(1),
        "monthly": Money.of(amount, kwargs.pop("currency", "USD")),
        "price": price(kwargs.pop("price_currency", "USD")),
        "model_id": "instance-hours",
        "model_version": 1,
        "assumptions": (Evidence("operating_hours_per_month", "730"),),
    }
    return LineItem(node, resource, **(fields | kwargs))


def unknown(node: str = "cache", resource: str = "instance_hours") -> LineItem:
    return LineItem(
        node,
        resource,
        CostCategory.DATABASE,
        CostKind.FIXED,
        LineStatus.UNKNOWN,
        missing=("pricing_sku",),
        reason="No price for this node's SKU in the snapshot.",
    )


def result(*lines: LineItem, unsupported: tuple[Unsupported, ...] = ()) -> CostResult:
    return CostResult(
        "USD",
        SNAPSHOT,
        "a" * 64,
        MODELS,
        "f" * 64,
        lines,
        unsupported,
        (Limitation("estimate_not_invoice", "Estimated from prices, not an invoice."),),
    )


def test_an_unknown_cost_is_never_zero() -> None:
    line = unknown()
    assert (line.monthly, line.priced, line.missing) == (None, False, ("pricing_sku",))
    with pytest.raises(InvalidCostResult):  # an unknown line cannot carry an amount
        LineItem(
            "db",
            "storage",
            CostCategory.STORAGE,
            CostKind.USAGE,
            LineStatus.UNKNOWN,
            monthly=Money.of(0, "USD"),
            missing=("x",),
        )
    with pytest.raises(InvalidCostResult):  # nor be unknown without saying why
        LineItem("db", "storage", CostCategory.STORAGE, CostKind.USAGE, LineStatus.UNKNOWN)
    with pytest.raises(InvalidCostResult):  # a priced line needs its amount, price and quantity
        LineItem(
            "db",
            "storage",
            CostCategory.STORAGE,
            CostKind.USAGE,
            LineStatus.PRICED,
            monthly=Money.of(1, "USD"),
        )


def test_a_priced_line_is_in_its_prices_currency() -> None:
    with pytest.raises(InvalidCostResult):
        priced(currency="EUR")  # an EUR amount from a USD price
    with pytest.raises(InvalidCostResult):
        priced(amount="-1")


@pytest.mark.parametrize(
    ("lines", "unsupported", "status", "complete"),
    [
        ((priced(), priced("api")), (), CostStatus.COMPLETED, True),
        ((priced(), unknown()), (), CostStatus.PARTIAL, False),
        ((priced(),), (Unsupported("queue", "unmapped", "No pricing mapping."),), CostStatus.PARTIAL, False),
        ((unknown(),), (), CostStatus.INSUFFICIENT_PRICING, False),
        ((), (Unsupported("queue", "unmapped", "No pricing mapping."),), CostStatus.UNSUPPORTED, False),
    ],
)
def test_the_status_and_completeness(
    lines: tuple[LineItem, ...], unsupported: tuple[Unsupported, ...], status: CostStatus, complete: bool
) -> None:
    r = result(*lines, unsupported=unsupported)
    assert (r.status, r.totals.complete) == (status, complete)


def test_totals_keep_the_known_subtotal_apart_from_unknowns() -> None:
    r = result(priced(), priced("api", amount="73"), unknown())
    totals = r.totals
    assert (totals.monthly, totals.unknown_items) == (Money.of("262.8", "USD"), 1)
    assert totals.hourly == Money.of("0.36", "USD")  # 262.8 / 730
    assert totals.annual == Money.of("3153.6", "USD")
    assert totals.to_dict()["monthly"]["display"] == "262.80"


def test_breakdowns_by_component_category_and_kind() -> None:
    r = result(
        priced("db", amount="150"),
        priced("api", amount="50", category=CostCategory.COMPUTE),
        priced("api", resource="requests", amount="50", category=CostCategory.NETWORK, kind=CostKind.USAGE),
        unknown("cache"),
    )
    by_component = {s.key: (s.monthly.amount, s.share, s.unknown_items) for s in r.by_component()}
    assert by_component == {
        "db": (Decimal(150), Decimal("0.6"), 0),
        "api": (Decimal(100), Decimal("0.4"), 0),
        "cache": (Decimal(0), Decimal(0), 1),
    }
    assert [s.key for s in r.by_component()] == ["db", "api", "cache"]  # largest first
    assert {s.key: s.monthly.amount for s in r.by_kind()} == {"fixed": Decimal(200), "usage": Decimal(50)}
    assert {s.key for s in r.by_category()} == {"database", "compute", "network"}


def test_a_zero_total_has_no_shares() -> None:
    assert [s.share for s in result(unknown()).by_component()] == [None]


def test_results_are_ordered_fingerprinted_and_round_trip() -> None:
    lines = (priced("db"), priced("api", amount="73"), unknown())
    one, other = result(*lines), result(*reversed(lines))
    assert one == other
    assert one.fingerprint == other.fingerprint
    assert CostResult.from_dict(one.to_dict()) == one
    with pytest.raises(InvalidCostResult):
        result(priced("db"), priced("db"))  # one line per (node, resource)


def request(**overrides: Any) -> CostAnalysisRequest:
    fields: dict[str, Any] = {
        "architecture_id": uuid.UUID(int=1),
        "revision_number": 3,
        "snapshot_id": SNAPSHOT,
        "currency": "USD",
        "pricing_date": date(2026, 9, 26),
    }
    return CostAnalysisRequest(**(fields | overrides))


def test_the_request_and_its_documented_defaults() -> None:
    r = request()
    assert (r.operating_hours_per_month, r.uptime) == (Decimal(730), Decimal(1))
    assert request(operating_hours_per_month=Decimal("365")).uptime == Decimal("0.5")
    assert r.inputs()["operating_hours_per_month"] == "730"
    assert {e.label for e in r.billing_assumptions()} == {
        "hours_per_month",
        "operating_hours_per_month",
        "pricing_date",
        "currency",
    }


@pytest.mark.parametrize(
    ("overrides", "field", "reason"),
    [
        ({"currency": "usd"}, "currency", "invalid_currency"),
        ({"revision_number": 0}, "revision_number", "not_a_positive_count"),
        ({"operating_hours_per_month": Decimal(0)}, "operating_hours_per_month", "out_of_range"),
        ({"operating_hours_per_month": Decimal(800)}, "operating_hours_per_month", "out_of_range"),
        ({"operating_hours_per_month": Decimal(-1)}, "operating_hours_per_month", "negative"),
        ({"pricing_date": NOW}, "pricing_date", "not_a_date"),
        (
            {"assumptions": (CostAssumption("a_b", "x"), CostAssumption("a_b", "y"))},
            "assumptions",
            "duplicate_key",
        ),
        ({"label": " "}, "label", "invalid_text"),
    ],
)
def test_invalid_requests_are_refused(overrides: dict[str, Any], field: str, reason: str) -> None:
    with pytest.raises(InvalidCostRequest) as raised:
        request(**overrides)
    assert raised.value.details == {"field": field, "reason": reason}


def analysis() -> CostAnalysis:
    return CostAnalysis(uuid.uuid7(), uuid.uuid7(), uuid.uuid7(), 1, "a" * 64, "pending", None, NOW)


def test_the_lifecycle() -> None:
    done = analysis().start(NOW).finish(result(priced(), unknown()), NOW)
    assert (done.status, done.totals is not None) == ("partial", True)
    failed = analysis().start(NOW).fail(CostAnalysisError("engine_error", "The analysis could not run."), NOW)
    assert (failed.status, failed.result) == ("failed", None)
    moves: tuple[Callable[[], object], ...] = (
        lambda: done.start(NOW),
        lambda: analysis().finish(result(priced()), NOW),
    )
    for move in moves:
        with pytest.raises(InvalidCostAnalysisTransition):
            move()
