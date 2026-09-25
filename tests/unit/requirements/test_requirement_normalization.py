from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest

from core.domain.requirements.errors import InvalidRequirement
from core.domain.requirements.normalization import (
    canonical,
    canonical_data,
    normalize_structured_data,
    normalize_unit,
    normalize_value,
    parse_quantity,
)
from core.domain.requirements.value_objects import QuantityConstraint, parse_structured_data


def constraint(raw: dict[str, Any]) -> QuantityConstraint:
    parsed = parse_structured_data(normalize_structured_data(raw))
    assert isinstance(parsed, QuantityConstraint)
    return parsed


# --- the spec's examples -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("2k requests/sec", "2000", "requests/second"),
        ("300 ms", "300", "ms"),
        ("99.9%", "99.9", "%"),
        ("10M users", "10000000", "users"),
        ("2,000 rps", "2000", "requests/second"),
        ("1.5M req/s", "1500000", "requests/second"),
        ("4 hours", "4", "h"),
        ("500 MB", "500", "MB"),
        ("500MB", "500", "MB"),
        ("2bn requests per day", "2000000000", "requests/day"),
        ("  30  Days ", "30", "d"),
        ("99.95 percent", "99.95", "%"),
        ("200 usd/month", "200", "USD/month"),
    ],
)
def test_quantities(text: str, value: str, unit: str) -> None:
    assert parse_quantity(text) == (value, unit)


def test_spec_examples_reach_their_canonical_values() -> None:
    cases = [
        (
            {"metric": "requests_per_second", "operator": ">=", "quantity": "2k requests/sec"},
            "2000",
            "requests/second",
        ),
        ({"metric": "latency", "operator": "<=", "quantity": "300 ms", "percentile": "p95"}, "300", "ms"),
        ({"metric": "availability", "operator": ">=", "quantity": "99.9%"}, "0.999", "ratio"),
        ({"metric": "daily_active_users", "operator": ">=", "quantity": "10M users"}, "10000000", "users"),
    ]
    for raw, value, unit in cases:
        data = canonical_data(constraint(raw))
        assert data is not None
        assert (data["value"], data["unit"]) == (value, unit), raw


def parse_input(raw: dict[str, Any]) -> object:
    return parse_structured_data(normalize_structured_data(raw))


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("5m", "ambiguous_unit"),  # milli, million or minutes?
        ("5 gb", "ambiguous_unit"),  # bits or bytes?
        ("5 b", "ambiguous_unit"),
        ("100 $/month", "ambiguous_unit"),
        ("2", "unit_required"),
        ("fast", "not_a_quantity"),
        ("2,5 s", "unknown_unit"),  # a decimal comma is not a thousands separator: ",5 s" is no unit
        ("2 k rps", "unknown_unit"),  # magnitude suffixes touch the number: "k rps" is no unit
    ],
)
def test_ambiguous_or_malformed_quantities_are_refused(text: str, reason: str) -> None:
    with pytest.raises(InvalidRequirement) as error:
        parse_input({"metric": "latency", "operator": "<=", "quantity": text})
    assert error.value.details["reason"] == reason


def test_a_quantity_cannot_be_combined_with_value_or_unit() -> None:
    with pytest.raises(InvalidRequirement) as error:
        normalize_structured_data({"metric": "latency", "operator": "<=", "quantity": "3 s", "unit": "ms"})
    assert error.value.details == {
        "field": "structured_data.quantity",
        "reason": "conflicts_with_value_and_unit",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2k", "2000"),
        ("1.5M", "1500000"),
        ("2,000", "2000"),
        ("10", "10"),
        (10, 10),
        ("2e3", "2e3"),
        ("2 k", "2 k"),
    ],
)
def test_values(raw: object, expected: object) -> None:
    assert normalize_value(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RPS", "requests/second"),
        ("Requests  Per  Second", "requests/second"),
        ("milliseconds", "ms"),
        ("MB", "MB"),
        ("eur/mo", "EUR/month"),
        ("furlongs", "furlongs"),  # passed through, refused by the strict parser
    ],
)
def test_units(raw: str, expected: str) -> None:
    assert normalize_unit(raw) == expected


def test_metric_and_percentile_aliases() -> None:
    normalized = normalize_structured_data({"metric": "RPS", "operator": ">=", "value": "2k", "unit": "rps"})
    assert normalized == {
        "metric": "requests_per_second",
        "operator": ">=",
        "value": "2000",
        "unit": "requests/second",
    }
    latency = normalize_structured_data(
        {"metric": "latency", "operator": "<=", "value": 1, "unit": "s", "percentile": "p99.9"}
    )
    assert isinstance(latency, dict)
    assert latency["percentile"] == "99.9"


def test_inputs_it_does_not_recognize_pass_through() -> None:
    assert normalize_structured_data({}) == {}
    assert normalize_structured_data([1]) == [1]
    assert normalize_structured_data({"metric": "regions", "operator": "in", "values": ["eu-west-1"]}) == {
        "metric": "regions",
        "operator": "in",
        "values": ["eu-west-1"],
    }


def test_normalization_is_idempotent() -> None:
    raw = {"metric": "rps", "operator": ">=", "quantity": "2k req/s"}
    once = normalize_structured_data(raw)
    assert normalize_structured_data(once) == once
    assert constraint(raw).to_dict() == normalize_structured_data(constraint(raw).to_dict())


# --- canonical form ------------------------------------------------------------------------------


def test_canonical_values_are_exact_when_the_conversion_terminates() -> None:
    quantity = canonical(constraint({"metric": "latency", "operator": "<=", "value": "1.5", "unit": "s"}))
    assert (quantity.value, quantity.unit, quantity.is_exact) == (Decimal(1500), "ms", True)


def test_non_terminating_conversions_are_rounded_for_display_only() -> None:
    quantity = canonical(
        constraint(
            {"metric": "requests_per_second", "operator": ">=", "value": "1000", "unit": "requests/minute"}
        )
    )
    assert quantity.exact == Fraction(50, 3)
    assert (str(quantity.value), quantity.is_exact) == ("16.666666667", False)


def test_money_keeps_its_currency() -> None:
    data = canonical_data(
        constraint({"metric": "monthly_budget", "operator": "<=", "value": "200", "unit": "EUR/month"})
    )
    assert data == {"metric": "monthly_budget", "operator": "<=", "value": "200", "unit": "EUR/month"}


def test_no_constraint_has_no_canonical_form() -> None:
    assert canonical_data(None) is None
