"""Cost aggregation and cost drivers (Milestone 8, phase 6): consistent breakdowns in one currency,
periods derived from monthly amounts, unknown items kept apart, drivers by explicit calculation."""

import dataclasses
from decimal import Decimal
from typing import Any

import pytest

from core.domain.cost.aggregation import Sensitivity, sensitivity, summarize
from core.domain.cost.errors import CurrencyMismatch, InvalidCostResult
from core.domain.cost.money import Money
from core.domain.cost.pricing import PricingModel
from core.domain.cost.results import CostCategory, CostKind, CostResult, CostStatus, LineItem, breakdown
from core.domain.engine_results import Evidence, Unsupported
from tests.unit.cost.test_cost_results import price, priced, result, unknown


def at(provider: str = "aws", region: str = "eu-west-1", model: PricingModel = PricingModel.PER_UNIT) -> Any:
    return dataclasses.replace(price(), provider=provider, region=region, model=model)


def shop() -> CostResult:
    return result(
        priced("db", amount="300"),
        priced("db", resource="storage", amount="20", category=CostCategory.STORAGE),
        priced("api", amount="100", category=CostCategory.COMPUTE, price=at(region="us-east-1")),
        priced("api", resource="requests", amount="40", category=CostCategory.COMPUTE, kind=CostKind.USAGE),
        priced("cdn", resource="data_transfer", amount="40", category=CostCategory.NETWORK,
               kind=CostKind.USAGE, price=at("cloudflare", "global", PricingModel.TIERED)),
        priced("workers", amount="0.5", category=CostCategory.COMPUTE,
               assumptions=(Evidence("instances", "2 (capacity)"), Evidence("declared_replicas", "1"))),
        unknown("cache"),
        unknown("db", resource="backups"),
    )  # fmt: skip


def keys(shares: Any) -> list[tuple[str | None, Decimal, int, int]]:
    return [(s.key, s.monthly.amount, s.priced_items, s.unknown_items) for s in shares]


def test_multiple_components_add_up_to_the_known_total() -> None:
    summary = summarize(shop())
    assert keys(summary.by_component) == [
        ("db", Decimal(320), 2, 1),
        ("api", Decimal(140), 2, 0),
        ("cdn", Decimal(40), 1, 0),
        ("workers", Decimal("0.5"), 1, 0),
        ("cache", Decimal(0), 0, 1),
    ]
    assert summary.totals.monthly.amount == Decimal("500.5")
    for breakdown_ in (summary.by_component, summary.by_resource, summary.by_category, summary.by_provider,
                       summary.by_region, summary.by_kind, summary.by_sensitivity):  # fmt: skip
        assert sum((s.monthly.amount for s in breakdown_), Decimal(0)) == summary.totals.monthly.amount
        assert sum(s.priced_items + s.unknown_items for s in breakdown_) == 8


def test_multiple_resource_categories_providers_and_regions() -> None:
    summary = summarize(shop())
    assert keys(summary.by_category) == [
        ("database", Decimal(300), 1, 2),
        ("compute", Decimal("140.5"), 3, 0),
        ("network", Decimal(40), 1, 0),
        ("storage", Decimal(20), 1, 0),
    ]
    assert keys(summary.by_resource)[0] == ("instance_hours", Decimal("400.5"), 3, 1)
    assert keys(summary.by_provider) == [("aws", Decimal("460.5"), 5, 0), ("cloudflare", Decimal(40), 1, 0),
                                         (None, Decimal(0), 0, 2)]  # fmt: skip
    assert [s.key for s in summary.by_region] == ["eu-west-1", "us-east-1", "global", None]
    assert keys(summary.by_kind) == [("fixed", Decimal("420.5"), 4, 2), ("usage", Decimal(80), 2, 0)]


def test_shares_are_of_the_known_total() -> None:
    by_component = {s.key: s.share for s in summarize(shop()).by_component}
    assert by_component["db"] == Decimal("0.639360639361")  # 320 / 500.5, 12 places half-even
    assert by_component["cache"] == Decimal(0)


def test_billing_periods_are_derived_from_monthly_amounts_never_added_across() -> None:
    summary = summarize(shop())
    totals = summary.totals.to_dict()
    assert (totals["monthly"]["amount"], totals["annual"]["amount"]) == ("500.5", "6006")  # x 12
    assert totals["hourly"]["amount"] == "0.685616438356"  # / 730
    db = summary.by_component[0].to_dict()
    assert (db["monthly"]["amount"], db["annual"]["amount"], db["hourly"]["amount"]) == (
        "320", "3840", "0.438356164384",
    )  # fmt: skip


def test_another_currency_is_never_combined() -> None:
    with pytest.raises(InvalidCostResult):  # a result is in one currency
        result(priced("db"), priced("api", currency="EUR", price_currency="EUR"))
    lines = [priced("db"), priced("api", currency="EUR", price_currency="EUR")]
    with pytest.raises(CurrencyMismatch):
        breakdown(lines, lambda line: line.element_id, "USD")


def test_unknown_items_are_listed_apart_and_the_known_total_is_a_lower_bound() -> None:
    summary = summarize(shop())
    assert [(u.element_id, u.resource, u.missing) for u in summary.unknown] == [
        ("cache", "instance_hours", ("pricing_sku",)),
        ("db", "backups", ("pricing_sku",)),
    ]
    assert summary.known_total_is_lower_bound
    assert summary.to_dict()["totals"]["complete"] is False
    assert summary.status is CostStatus.PARTIAL
    complete = summarize(result(priced("db")))
    assert (complete.known_total_is_lower_bound, complete.unknown) == (False, ())


def test_unsupported_components_make_the_total_a_lower_bound() -> None:
    summary = summarize(
        dataclasses.replace(result(priced("db")), unsupported=(Unsupported("vm", "on_premises", "x"),))
    )
    assert summary.known_total_is_lower_bound


def test_drivers_are_explicit_calculations() -> None:
    drivers = summarize(shop(), top=3).drivers
    assert drivers.largest_component is not None
    assert (drivers.largest_component.key, drivers.largest_component.monthly.amount) == ("db", Decimal(320))
    assert drivers.largest_category is not None
    assert drivers.largest_category.key == "database"
    assert [(i.element_id, i.resource, i.monthly.amount) for i in drivers.top_items] == [
        ("db", "instance_hours", Decimal(300)),
        ("api", "instance_hours", Decimal(100)),
        ("api", "requests", Decimal(40)),  # ties with cdn: by component, then resource
    ]
    assert (drivers.fixed.amount, drivers.usage.amount) == (Decimal("420.5"), Decimal(80))
    assert drivers.workload_sensitive.amount == Decimal("80.5")  # usage + capacity-driven replicas
    assert set(drivers.to_dict()) == {
        "largest_component", "largest_category", "top_items", "fixed", "usage", "workload_sensitive",
    }  # fmt: skip


def test_workload_sensitivity_of_each_line() -> None:
    found = {(line.element_id, line.resource): sensitivity(line) for line in shop().line_items}
    assert found == {
        ("api", "instance_hours"): Sensitivity.FIXED,
        ("api", "requests"): Sensitivity.LINEAR,
        ("cdn", "data_transfer"): Sensitivity.TIERED,
        ("workers", "instance_hours"): Sensitivity.STEPWISE,
        ("db", "instance_hours"): Sensitivity.FIXED,
        ("db", "storage"): Sensitivity.FIXED,
        ("db", "backups"): None,
        ("cache", "instance_hours"): None,
    }
    assert keys(summarize(shop()).by_sensitivity) == [
        ("fixed", Decimal(420), 3, 0), ("linear", Decimal(40), 1, 0), ("tiered", Decimal(40), 1, 0),
        ("stepwise", Decimal("0.5"), 1, 0), (None, Decimal(0), 0, 2),
    ]  # fmt: skip


def test_drivers_never_judge_a_cost() -> None:
    text = str(summarize(shop()).to_dict()).lower()
    assert not any(word in text for word in ("inefficient", "wasteful", "waste", "overprovisioned"))


def test_the_summary_is_stable() -> None:
    shuffled = dataclasses.replace(shop(), line_items=tuple(reversed(shop().line_items)))
    assert summarize(shuffled).to_dict() == summarize(shop()).to_dict()


def test_an_empty_result_has_no_drivers() -> None:
    summary = summarize(result())
    assert (summary.drivers.largest_component, summary.drivers.top_items, summary.totals.monthly) == (
        None, (), Money.zero("USD"),
    )  # fmt: skip


def test_lines_are_traceable_from_every_driver() -> None:
    items = summarize(shop()).drivers.top_items
    lines = {(line.element_id, line.resource): line for line in shop().line_items}
    for item in items:
        line: LineItem = lines[item.element_id, item.resource]
        assert line.monthly == item.monthly
