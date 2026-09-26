"""Capacity quantities (Milestone 7, phase 1): explicit units, exact conversions, no mixing."""

from decimal import Decimal

import pytest

from core.domain.capacity.errors import InvalidQuantity
from core.domain.capacity.units import CANONICAL, UNITS, Dimension, Quantity, exact


@pytest.mark.parametrize(
    ("value", "unit", "canonical"),
    [
        ("120", "requests/minute", Decimal(2)),
        ("7200", "requests/hour", Decimal(2)),
        ("1.5", "MB/s", Decimal(1_500_000)),
        ("2", "GB", Decimal(2_000_000_000)),
        ("1.5", "s", Decimal(1500)),
        ("500", "millicores", Decimal("0.5")),
        ("75", "%", Decimal("0.75")),
        ("3", "connections", Decimal(3)),
    ],
)
def test_canonical_values_are_exact(value: str, unit: str, canonical: Decimal) -> None:
    assert Quantity.of(value, unit).canonical == canonical


def test_every_dimension_has_a_canonical_unit_in_the_table() -> None:
    assert set(CANONICAL) == set(Dimension)
    assert all(UNITS[symbol].dimension is dimension for dimension, symbol in CANONICAL.items())


def test_conversions_round_trip_and_never_cross_dimensions() -> None:
    rate = Quantity.of("90", "requests/minute")
    assert rate.to("requests/second") == Quantity.of("1.5", "requests/second")
    assert rate.to("requests/second").to("requests/minute") == rate
    assert Quantity.of("1", "requests/day").to("requests/second").value == Decimal("0.000011574")  # 9 places
    with pytest.raises(InvalidQuantity) as raised:
        rate.to("MB/s")
    assert raised.value.details["reason"] == "incompatible_dimension"


@pytest.mark.parametrize(
    ("value", "unit", "reason"),
    [
        ("-1", "requests/second", "negative"),
        ("NaN", "requests/second", "not_a_number"),
        ("Infinity", "B", "not_a_number"),
        (True, "B", "not_a_number"),
        ("1e15", "B", "out_of_range"),
        ("0.0000000001", "B", "too_precise"),
        ("abc", "B", "not_a_number"),
        ("1", "rps", "unknown_unit"),
        ("1", "mb", "unknown_unit"),  # units are case-sensitive and never guessed
    ],
)
def test_invalid_quantities_are_refused(value: object, unit: str, reason: str) -> None:
    with pytest.raises(InvalidQuantity) as raised:
        Quantity.of(value, unit)
    assert raised.value.details["reason"] == reason


def test_values_are_canonical_decimals() -> None:
    assert exact(0.1, "x") == Decimal("0.1")  # through the float's shortest representation
    assert exact("2.500", "x") == Decimal("2.5")
    assert exact("-0", "x") == Decimal(0)
    assert Quantity.of("2.50", "MB") == Quantity.of("2.5", "MB")


def test_serialization_is_explicit_and_strict() -> None:
    quantity = Quantity.of("1500.5", "requests/second")
    assert quantity.to_dict() == {"value": "1500.5", "unit": "requests/second"}
    assert Quantity.from_dict(quantity.to_dict()) == quantity
    for bad in ({"value": "1"}, {"value": "1", "unit": "B", "extra": 1}, "1 B", None):
        with pytest.raises(InvalidQuantity):
            Quantity.from_dict(bad)


def test_rounding_of_calculated_values_is_deterministic() -> None:
    third = Decimal(1) / Decimal(3)
    assert Quantity.rounded(third, "requests/second").value == Decimal("0.333333333")
    assert Quantity.rounded(Decimal("0.0000000005"), "B").value == Decimal(0)  # half-even
    assert Quantity.rounded(Decimal("0.0000000015"), "B").value == Decimal("0.000000002")
