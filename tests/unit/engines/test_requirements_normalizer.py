from decimal import Decimal

import pytest

from core.domain.requirements.normalization import canonical_data
from core.domain.requirements.value_objects import Operator, parse_structured_data
from engines.requirements.normalizer import (
    DOLLAR_AS_USD,
    MONTH_AS_30_DAYS,
    YEAR_AS_365_DAYS,
    Bound,
    normalize,
)


def only_bound(text: str) -> Bound:
    result = normalize(text)
    assert result.problems == (), result.problems
    [bound] = result.bounds
    return bound


def reading(text: str) -> tuple[str | None, str, str | None]:
    bound = only_bound(text)
    assert bound.quantity is not None
    unit = bound.quantity.unit
    return (
        bound.operator.value if bound.operator else None,
        str(bound.quantity.value),
        unit.symbol if unit else None,
    )


# --- magnitudes and numbers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("100K users", "100000"),
        ("1M users", "1000000"),
        ("2k rps", "2000"),
        ("1.5M users", "1500000"),
        ("2,000 rps", "2000"),
        ("10 million users", "10000000"),
        ("2 billion requests per day", "2000000000"),
        ("3 thousand users", "3000"),
        ("2bn requests/day", "2000000000"),
    ],
)
def test_magnitudes(text: str, value: str) -> None:
    assert reading(text)[1] == value


# --- units -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "unit"),
    [
        ("2000 requests/sec", "requests/second"),
        ("2000 requests per second", "requests/second"),
        ("2000 req/s", "requests/second"),
        ("2000 RPS", "requests/second"),
        ("120000 requests per minute", "requests/minute"),
        ("500 orders/sec", "orders/second"),
        ("30000 orders per hour", "orders/hour"),
        ("300ms", "ms"),
        ("300 milliseconds", "ms"),
        ("2 seconds", "s"),
        ("5 minutes", "min"),
        ("4 hours", "h"),
        ("30 days", "d"),
        ("99.9%", "%"),
        ("99.9 percent", "%"),
        ("10GB", "GB"),
        ("500 MB", "MB"),
        ("2 TB", "TB"),
        ("100 bytes", "B"),
        ("100 EUR/month", "EUR/month"),
        ("EUR 100 per month", "EUR/month"),
        ("100 users", "users"),
    ],
)
def test_units(text: str, unit: str) -> None:
    assert reading(text)[2] == unit


def test_canonical_forms_come_from_the_domain() -> None:
    """seconds → milliseconds, GB → bytes, % → ratio: one conversion table for text and JSON."""
    cases = [
        ("latency", "1.5 seconds", "1500", "ms"),
        ("storage", "10GB", "10000000000", "B"),
        ("availability", "99.9%", "0.999", "ratio"),
        ("requests_per_second", "120000 requests per minute", "2000", "requests/second"),
    ]
    for metric, text, value, unit in cases:
        data = only_bound(text).structured_data(metric, Operator.AT_LEAST)
        constraint = parse_structured_data(data)
        canonical = canonical_data(constraint)
        assert canonical is not None
        assert (canonical["value"], canonical["unit"]) == (value, unit), text


def test_users_carry_their_qualifier() -> None:
    assert only_bound("100K daily active users").qualifier == "daily active"
    assert only_bound("100K DAU").qualifier == "daily active"
    assert only_bound("10M MAU").qualifier == "monthly active"
    assert only_bound("5000 concurrent users").qualifier == "concurrent"
    assert only_bound("10M users").qualifier is None  # which users? the assumption engine asks


def test_a_number_without_a_known_unit_keeps_its_noun_and_is_not_guessed() -> None:
    bound = only_bound("handle 2000 connections")
    assert bound.unit is None
    assert bound.noun == "connections"
    with pytest.raises(ValueError, match="unit"):
        bound.structured_data("requests_per_second", Operator.AT_LEAST)


# --- interpretations -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value", "unit", "interpretation"),
    [
        ("retain data for 7 years", "2555", "d", YEAR_AS_365_DAYS),
        ("keep backups for 6 months", "180", "d", MONTH_AS_30_DAYS),
        ("$500 per month", "500", "USD/month", DOLLAR_AS_USD),
    ],
)
def test_conversions_that_assume_something_say_so(
    text: str, value: str, unit: str, interpretation: object
) -> None:
    bound = only_bound(text)
    assert bound.quantity is not None
    assert (str(bound.quantity.value), bound.unit.symbol if bound.unit else None) == (value, unit)
    assert bound.interpretations == (interpretation,)


def test_exact_conversions_assume_nothing() -> None:
    assert only_bound("2 weeks").interpretations == ()
    assert str(only_bound("2 weeks").quantity.value) == "14"  # type: ignore[union-attr]
    assert only_bound("€20/month").interpretations == ()


@pytest.mark.parametrize(
    ("text", "value"), [("two nines", "99"), ("three nines", "99.9"), ("four nines", "99.99")]
)
def test_nines(text: str, value: str) -> None:
    assert reading(text) == (None, value, "%")


# --- operators -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "operator"),
    [
        ("at least 2,000 rps", ">="),
        ("no less than 2000 rps", ">="),
        ("a minimum of 2000 rps", ">="),
        ("2000 rps or more", ">="),
        ("at most 300ms", "<="),
        ("no more than 300ms", "<="),
        ("up to 300ms", "<="),
        ("within 300ms", "<="),
        ("300ms or less", "<="),
        ("less than 300ms", "<"),
        ("under 300ms", "<"),
        ("below 300ms", "<"),
        ("more than 2000 rps", ">"),
        ("over 2000 rps", ">"),
        ("above 2000 rps", ">"),
        ("exactly 99.9%", "=="),
        ("2000 rps", None),
        ("support 2000 rps", None),
    ],
)
def test_operator_phrases(text: str, operator: str | None) -> None:
    assert reading(text)[0] == operator


def test_an_operator_phrase_spans_with_its_bound() -> None:
    text = "The API p95 latency must be under 300 ms."
    bound = only_bound(text)
    assert text[bound.start : bound.end] == "under 300 ms"


# --- ranges ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "low", "high", "unit"),
    [
        ("between 10GB and 20GB", "10", "20", "GB"),
        ("between 10 and 20 GB", "10", "20", "GB"),
        ("from 1k to 5k rps", "1000", "5000", "requests/second"),
        ("10-20 GB", "10", "20", "GB"),
        ("100 to 200 ms", "100", "200", "ms"),
    ],
)
def test_ranges(text: str, low: str, high: str, unit: str) -> None:
    bound = only_bound(text)
    assert bound.operator is Operator.BETWEEN
    assert bound.minimum is not None
    assert bound.maximum is not None
    assert (
        str(bound.minimum.value),
        str(bound.maximum.value),
        bound.unit.symbol if bound.unit else None,
    ) == (
        low,
        high,
        unit,
    )
    data = bound.structured_data("storage" if unit == "GB" else "x", Operator.AT_LEAST)
    assert data["operator"] == "between"


def test_two_separate_quantities_are_not_a_range() -> None:
    result = normalize("Support 100K DAU and 2K RPS.")
    assert [(str(b.quantity.value), b.unit.symbol) for b in result.bounds if b.quantity and b.unit] == [
        ("100000", "users"),
        ("2000", "requests/second"),
    ]


# --- percentiles -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("p95 latency under 300ms", "95"),
        ("99th percentile under 1 s", "99"),
        ("P99.9 below 2s", "99.9"),
        ("median under 50ms", "50"),
    ],
)
def test_percentiles_are_found_and_are_not_quantities(text: str, value: str) -> None:
    result = normalize(text)
    assert [str(p.value) for p in result.percentiles] == [value]
    assert len(result.bounds) == 1  # the 95 of p95 is not a quantity
    assert (
        result.bounds[0].structured_data("latency", Operator.AT_MOST, result.percentiles[0].value)[
            "percentile"
        ]
        == value
    )


# --- refusals --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("5m users", "ambiguous_unit"),
        ("store 10 gb", "ambiguous_unit"),
        ("store 10 GiB", "unsupported_unit"),
        ("-50 rps", "negative_value"),
        ("latency of \u22125 ms", "negative_value"),
        ("1000000000000000 rps", "out_of_range"),
        ("0.0000000001 s", "too_precise"),
        ("$500", "unsupported_unit"),
    ],
)
def test_problems_are_reported_not_guessed(text: str, reason: str) -> None:
    result = normalize(text)
    assert [p.reason for p in result.problems] == [reason]
    assert result.bounds == ()
    problem = result.problems[0]
    assert text[problem.start : problem.end] == problem.text


def test_the_text_is_never_rewritten_and_every_span_is_exact() -> None:
    text = (
        "Our food delivery platform should support 100K DAU, 2K RPS, 500 orders/sec, "
        "p95 latency below 300ms and 99.9% availability."
    )
    result = normalize(text)
    assert len(result.bounds) == 5
    for bound in result.bounds:
        for quantity in (bound.quantity, bound.minimum, bound.maximum):
            if quantity is not None:
                assert text[quantity.start : quantity.end].strip()
    assert [text[b.start : b.end] for b in result.bounds] == [
        "100K DAU",
        "2K RPS",
        "500 orders/sec",
        "below 300ms",
        "99.9%",
    ]


def test_normalization_is_deterministic() -> None:
    text = "at least 2k rps, between 10 and 20 GB, p99 under 1.5 s"
    assert normalize(text) == normalize(text)


def test_values_are_exact_decimals() -> None:
    assert only_bound("99.95%").quantity.value == Decimal("99.95")  # type: ignore[union-attr]
