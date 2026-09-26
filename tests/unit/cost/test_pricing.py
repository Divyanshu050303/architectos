"""Pricing records and snapshots (Milestone 8, phase 1): provenance, models, validation."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.domain.cost.errors import InvalidPricingRecord, InvalidPricingSnapshot
from core.domain.cost.pricing import (
    PricingModel,
    PricingRecord,
    PricingSnapshot,
    PricingSource,
    PricingUnit,
    Tier,
)

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


def record(**overrides: Any) -> PricingRecord:
    fields: dict[str, Any] = {
        "id": "rds-r6g-large",
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
        "retrieved_at": NOW,
        "conditions": ("on_demand", "single_az"),
    }
    return PricingRecord(**(fields | overrides))


def requests(**overrides: Any) -> PricingRecord:
    return record(
        id="apigw-requests",
        service="apigateway",
        sku="ApiGatewayRequest",
        unit=PricingUnit.REQUEST,
        unit_price=Decimal("3.50"),
        per=Decimal(1_000_000),
        **overrides,
    )


def test_a_per_unit_price_is_charged_exactly() -> None:
    assert record().charge(Decimal(730)) == Decimal("189.8")  # 730 instance hours
    assert requests().charge(Decimal(250_000)) == Decimal("0.875")  # 3.50 per million
    assert record().charge(Decimal(0)) == Decimal(0)


def test_a_fixed_price_is_charged_per_period() -> None:
    support = record(
        id="support",
        service="support",
        sku="business",
        unit=PricingUnit.MONTH,
        model=PricingModel.FIXED,
        unit_price=Decimal(100),
    )
    assert support.charge(Decimal(1)) == Decimal(100)
    assert support.charge(Decimal(12)) == Decimal(1200)


def test_tiers_are_graduated() -> None:
    storage = record(
        id="s3-standard",
        service="s3",
        sku="TimedStorage-ByteHrs",
        unit=PricingUnit.GB_MONTH,
        model=PricingModel.TIERED,
        unit_price=None,
        tiers=(
            Tier(Decimal(50_000), Decimal("0.023")),
            Tier(Decimal(500_000), Decimal("0.022")),
            Tier(None, Decimal("0.021")),
        ),
    )
    assert storage.charge(Decimal(1000)) == Decimal(23)  # all in the first tier
    assert storage.charge(Decimal(50_000)) == Decimal(1150)  # exactly on the boundary
    assert storage.charge(Decimal(60_000)) == Decimal(1150 + 220)  # 10,000 GB at 0.022
    assert storage.charge(Decimal(600_000)) == Decimal(1150) + Decimal(450_000) * Decimal("0.022") + Decimal(
        100_000
    ) * Decimal("0.021")


@pytest.mark.parametrize(
    ("overrides", "field", "reason"),
    [
        ({"unit_price": Decimal("-0.1")}, "unit_price", "negative"),
        ({"unit_price": None}, "unit_price", "required"),
        ({"currency": "usd"}, "currency", "invalid_currency"),
        ({"region": "EU West"}, "region", "invalid_identifier"),
        ({"provider": "AWS"}, "provider", "invalid_identifier"),
        ({"sku": ""}, "sku", "invalid_identifier"),
        ({"per": Decimal(0)}, "per", "must_be_positive"),
        ({"unit": PricingUnit.MONTH}, "unit", "not_for_model"),
        ({"model": PricingModel.FIXED}, "unit", "not_for_model"),
        ({"effective_to": date(2026, 8, 1)}, "effective_to", "not_after_effective_from"),
        ({"effective_from": datetime(2026, 9, 1, tzinfo=UTC)}, "effective_from", "not_a_date"),
        ({"retrieved_at": datetime(2026, 9, 1)}, "retrieved_at", "not_an_aware_datetime"),  # noqa: DTZ001
        ({"conditions": ("On Demand",)}, "conditions", "invalid_identifier"),
        ({"model": PricingModel.TIERED}, "unit_price", "not_for_model"),
        ({"model": PricingModel.TIERED, "unit_price": None, "tiers": ()}, "tiers", "required"),
        (
            {
                "model": PricingModel.TIERED,
                "unit_price": None,
                "tiers": (
                    Tier(Decimal(10), Decimal(1)),
                    Tier(Decimal(5), Decimal(1)),
                    Tier(None, Decimal(1)),
                ),
            },
            "tiers",
            "not_ascending",
        ),
        (
            {"model": PricingModel.TIERED, "unit_price": None, "tiers": (Tier(Decimal(10), Decimal(1)),)},
            "tiers",
            "only_the_last_tier_is_open",
        ),
        ({"tiers": (Tier(None, Decimal(1)),)}, "tiers", "not_for_model"),
    ],
)
def test_invalid_records_are_refused(overrides: dict[str, Any], field: str, reason: str) -> None:
    with pytest.raises(InvalidPricingRecord) as raised:
        record(**overrides)
    assert raised.value.details == {"field": field, "reason": reason}


def test_negative_quantities_are_refused() -> None:
    with pytest.raises(InvalidPricingRecord):
        record().charge(Decimal(-1))


def test_records_round_trip_and_are_strict() -> None:
    original = record(effective_to=date(2027, 1, 1), description="On-demand, single AZ")
    assert PricingRecord.from_dict(original.to_dict()) == original
    with pytest.raises(InvalidPricingRecord) as raised:
        PricingRecord.from_dict(original.to_dict() | {"discount": "10%"})
    assert raised.value.details == {"field": "discount", "reason": "unknown_field"}
    with pytest.raises(InvalidPricingRecord):
        PricingRecord.from_dict(original.to_dict() | {"unit": "gigabyte"})


def test_effective_periods() -> None:
    dated = record(effective_to=date(2026, 12, 1))
    assert (dated.effective_on(date(2026, 9, 1)), dated.effective_on(date(2026, 11, 30))) == (True, True)
    assert (dated.effective_on(date(2026, 8, 31)), dated.effective_on(date(2026, 12, 1))) == (False, False)


def snapshot(*records: PricingRecord) -> PricingSnapshot:
    return PricingSnapshot(
        uuid.UUID(int=1), uuid.UUID(int=2), "EU list prices", records or (record(), requests()), NOW
    )


def test_snapshots_are_ordered_hashed_and_unique() -> None:
    one, other = snapshot(record(), requests()), snapshot(requests(), record())
    assert one.records == other.records
    assert one.content_hash == other.content_hash
    assert snapshot(record(unit_price=Decimal("0.27")), requests()).content_hash != one.content_hash
    assert one.record("apigw-requests") == requests()
    with pytest.raises(InvalidPricingSnapshot):
        snapshot(record(), record(region="us-east-1"))  # same id twice
    with pytest.raises(InvalidPricingSnapshot):
        PricingSnapshot(uuid.UUID(int=1), uuid.UUID(int=2), "Empty", (), NOW)
