"""Price lookup (Milestone 8, phase 2): exact, deterministic, and explicit about every mismatch."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.domain.cost.lookup import Freshness, LookupOutcome, PriceQuery, lookup
from core.domain.cost.pricing import PricingModel, PricingRecord, PricingSnapshot, PricingSource, PricingUnit

DAY = date(2026, 9, 26)


def record(id: str = "rds-large", **overrides: Any) -> PricingRecord:
    fields: dict[str, Any] = {
        "id": id,
        "provider": "aws",
        "service": "rds",
        "sku": "db.r6g.large",
        "region": "eu-west-1",
        "currency": "USD",
        "unit": PricingUnit.INSTANCE_HOUR,
        "model": PricingModel.PER_UNIT,
        "unit_price": Decimal("0.26"),
        "effective_from": date(2026, 9, 1),
        "source": PricingSource.USER_INPUT,
        "retrieved_at": datetime(2026, 9, 20, tzinfo=UTC),
        "conditions": ("on_demand",),
    }
    return PricingRecord(**(fields | overrides))


def snapshot(*records: PricingRecord) -> PricingSnapshot:
    return PricingSnapshot(
        uuid.UUID(int=1), uuid.UUID(int=2), "Prices", records, datetime(2026, 9, 26, tzinfo=UTC)
    )


def query(**overrides: Any) -> PriceQuery:
    fields: dict[str, Any] = {
        "provider": "aws",
        "service": "rds",
        "sku": "db.r6g.large",
        "region": "eu-west-1",
        "currency": "USD",
        "on": DAY,
        "unit": PricingUnit.INSTANCE_HOUR,
    }
    return PriceQuery(**(fields | overrides))


def test_an_exact_match() -> None:
    found = lookup(snapshot(record(), record("other", sku="db.r6g.xlarge")), query())
    assert (found.outcome, found.record.id if found.record else None) == (LookupOutcome.FOUND, "rds-large")
    assert found.freshness == Freshness(date(2026, 9, 1), datetime(2026, 9, 20, tzinfo=UTC), 6, stale=False)


def test_another_region_is_reported_never_substituted() -> None:
    result = lookup(snapshot(record(region="us-east-1"), record("b", region="eu-central-1")), query())
    assert (result.outcome, result.record, result.detail) == (
        LookupOutcome.REGION_MISMATCH,
        None,
        "eu-central-1, us-east-1",
    )
    assert "only in eu-central-1, us-east-1" in result.message


def test_another_currency_is_reported_never_converted() -> None:
    result = lookup(snapshot(record(currency="EUR")), query())
    assert (result.outcome, result.record, result.detail) == (LookupOutcome.CURRENCY_MISMATCH, None, "EUR")
    assert "no conversion" in result.message


def test_missing_prices_name_what_is_missing() -> None:
    result = lookup(snapshot(record()), query(sku="db.m7g.large"))
    assert (result.outcome, result.missing) == (LookupOutcome.NOT_FOUND, ("price",))
    assert "db.m7g.large" in result.message
    conditions = lookup(snapshot(record()), query(conditions=frozenset({"reserved_1y"})))
    assert (conditions.outcome, conditions.missing) == (LookupOutcome.NOT_FOUND, ("price.conditions",))


def test_prices_not_effective_on_the_day() -> None:
    future = lookup(snapshot(record(effective_from=date(2026, 10, 1))), query())
    expired = lookup(
        snapshot(record(effective_from=date(2026, 1, 1), effective_to=date(2026, 9, 1))), query()
    )
    assert future.outcome is expired.outcome is LookupOutcome.NOT_EFFECTIVE


def test_the_latest_effective_price_wins_and_ties_are_ambiguous() -> None:
    old = record("rds-2026-01", effective_from=date(2026, 1, 1), unit_price=Decimal("0.30"))
    new = record("rds-2026-09", effective_from=date(2026, 9, 1), unit_price=Decimal("0.26"))
    found = lookup(snapshot(old, new), query())
    assert found.record is not None
    assert found.record.id == "rds-2026-09"
    historical = lookup(snapshot(old, new), query(on=date(2026, 6, 1)))
    assert historical.record is not None
    assert historical.record.id == "rds-2026-01"  # the price in force then
    twin = record("rds-twin", unit_price=Decimal("0.27"), conditions=("on_demand", "multi_az"))
    tie = lookup(snapshot(new, twin), query())
    assert (tie.outcome, tie.record, tie.candidates) == (
        LookupOutcome.AMBIGUOUS,
        None,
        ("rds-2026-09", "rds-twin"),
    )
    narrowed = lookup(snapshot(new, twin), query(conditions=frozenset({"multi_az"})))
    assert narrowed.record is not None
    assert narrowed.record.id == "rds-twin"


def test_the_unit_must_be_the_one_the_calculation_needs() -> None:
    result = lookup(snapshot(record()), query(unit=PricingUnit.GB_MONTH))
    assert (result.outcome, result.detail) == (LookupOutcome.UNIT_MISMATCH, "instance_hour")


@pytest.mark.parametrize(
    ("retrieved_at", "age", "stale"),
    [
        (datetime(2026, 9, 20, tzinfo=UTC), 6, False),
        (datetime(2026, 6, 28, tzinfo=UTC), 90, False),
        (datetime(2026, 6, 27, tzinfo=UTC), 91, True),
        (None, None, True),  # unknown retrieval is never presented as current
    ],
)
def test_freshness(retrieved_at: datetime | None, age: int | None, stale: bool) -> None:
    found = lookup(snapshot(record(retrieved_at=retrieved_at)), query())
    assert found.freshness is not None
    assert (found.freshness.age_days, found.freshness.stale) == (age, stale)
    strict = lookup(snapshot(record(retrieved_at=retrieved_at)), query(), max_age_days=5)
    assert strict.freshness is not None
    assert strict.freshness.stale is True


def test_lookups_are_deterministic_whatever_the_record_order() -> None:
    records = (record("a", region="us-east-1"), record("b"), record("c", sku="db.r6g.xlarge"))
    assert lookup(snapshot(*records), query()) == lookup(snapshot(*reversed(records)), query())
