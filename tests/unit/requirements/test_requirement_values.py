from decimal import Decimal

import pytest

from core.domain.requirements.errors import InvalidRequirement
from core.domain.requirements.value_objects import (
    UNITS,
    Dimension,
    Operator,
    QuantityConstraint,
    SetConstraint,
    decimal_to_str,
    normalize_change_reason,
    normalize_identifier,
    normalize_statement,
    normalize_title,
    parse_confidence,
    parse_decimal,
    parse_structured_data,
    unit_for,
)


def reason_of(error: pytest.ExceptionInfo[InvalidRequirement]) -> tuple[str, str]:
    details = error.value.details
    return details["field"], details["reason"]


# --- text ----------------------------------------------------------------------------------------


def test_titles_are_trimmed_and_whitespace_collapsed() -> None:
    assert normalize_title("  API   throughput \n") == "API throughput"


@pytest.mark.parametrize(("raw", "reason"), [("", "length"), ("   ", "length"), ("x" * 201, "length")])
def test_title_length(raw: str, reason: str) -> None:
    with pytest.raises(InvalidRequirement) as error:
        normalize_title(raw)
    assert reason_of(error) == ("title", reason)


def test_statements_keep_line_breaks_but_refuse_other_control_characters() -> None:
    assert normalize_statement(" line one\r\nline two\t. ") == "line one\nline two\t."
    with pytest.raises(InvalidRequirement) as error:
        normalize_statement("rm" + chr(0x202E) + "txt")
    assert reason_of(error) == ("statement", "control_characters")
    with pytest.raises(InvalidRequirement):
        normalize_statement("x" * 5001)


def test_blank_change_reasons_mean_no_reason() -> None:
    assert normalize_change_reason(None) is None
    assert normalize_change_reason("   ") is None
    assert normalize_change_reason(" Forecast grew ") == "Forecast grew"
    with pytest.raises(InvalidRequirement):
        normalize_change_reason("x" * 501)


@pytest.mark.parametrize(
    ("raw", "expected"), [(" Throughput ", "throughput"), ("rpo", "rpo"), ("pci_dss", "pci_dss")]
)
def test_identifiers_are_trimmed_and_lower_cased(raw: str, expected: str) -> None:
    assert normalize_identifier(raw, "category") == expected


@pytest.mark.parametrize("raw", ["", "p95 latency", "p95-latency", "1st", "x" * 65, "latência", 5, None])
def test_identifiers_are_never_rewritten(raw: object) -> None:
    with pytest.raises(InvalidRequirement):
        normalize_identifier(raw, "category")


# --- numbers -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (2000, "2000"),
        ("2000", "2000"),
        (" 2e3 ", "2000"),
        (99.9, "99.9"),  # a float goes through its shortest repr: no 99.90000000000000568
        (0.1, "0.1"),
        (Decimal("0.99900"), "0.999"),
        ("-0", "0"),
        ("0.000000001", "0.000000001"),
    ],
)
def test_numbers_are_exact_decimals(raw: object, expected: str) -> None:
    assert decimal_to_str(parse_decimal(raw, "structured_data.value")) == expected


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (True, "not_a_number"),
        ("fast", "not_a_number"),
        ("NaN", "not_a_number"),
        ("Infinity", "not_a_number"),
        (float("inf"), "not_a_number"),
        ([1], "not_a_number"),
        (None, "not_a_number"),
        ("1e15", "out_of_range"),
        ("0.0000000001", "too_precise"),
    ],
)
def test_invalid_numbers(raw: object, reason: str) -> None:
    with pytest.raises(InvalidRequirement) as error:
        parse_decimal(raw, "structured_data.value")
    assert reason_of(error) == ("structured_data.value", reason)


@pytest.mark.parametrize("raw", [0, 1, "0.7", 0.95, "0.125"])
def test_valid_confidence(raw: object) -> None:
    assert Decimal(0) <= parse_confidence(raw) <= Decimal(1)


@pytest.mark.parametrize(
    ("raw", "reason"), [(2.0, "out_of_range"), (-0.1, "out_of_range"), ("0.1234", "too_precise")]
)
def test_invalid_confidence(raw: object, reason: str) -> None:
    with pytest.raises(InvalidRequirement) as error:
        parse_confidence(raw)
    assert reason_of(error) == ("confidence", reason)


# --- units ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "symbol", "canonical"),
    [
        ("300", "ms", "300"),
        ("1.5", "s", "1500"),
        ("4", "h", "14400000"),
        ("99.9", "%", "0.999"),
        ("0.9999", "ratio", "0.9999"),
        ("120000", "requests/minute", "2000"),
        ("2", "TB", "2000000000000"),
    ],
)
def test_conversion_to_the_canonical_unit_is_exact(value: str, symbol: str, canonical: str) -> None:
    assert decimal_to_str(UNITS[symbol].to_canonical(Decimal(value))) == canonical


def test_every_dimension_but_money_has_a_canonical_unit_in_the_table() -> None:
    for unit in UNITS.values():
        assert unit.canonical_symbol in UNITS
        assert UNITS[unit.canonical_symbol].dimension is unit.dimension


def test_money_units_name_their_currency() -> None:
    unit = unit_for("EUR/month")
    assert (unit.dimension, unit.canonical_symbol) == (Dimension.MONEY_PER_MONTH, "EUR/month")


@pytest.mark.parametrize(
    "symbol", ["mb", "rps", "req/s", "seconds", "percent", "eur/month", "EURO/month", "", 5]
)
def test_unknown_units_are_refused_not_guessed(symbol: object) -> None:
    with pytest.raises(InvalidRequirement) as error:
        unit_for(symbol)
    assert error.value.details["field"] == "structured_data.unit"


# --- structured data -----------------------------------------------------------------------------


def test_empty_structured_data_means_no_constraint() -> None:
    assert parse_structured_data({}) is None


def test_a_quantity_constraint_round_trips() -> None:
    raw = {"metric": "latency", "operator": "<=", "value": 300, "unit": "ms", "percentile": 95}
    constraint = parse_structured_data(raw)
    assert isinstance(constraint, QuantityConstraint)
    assert (constraint.operator, constraint.value, constraint.percentile) == (
        Operator.AT_MOST,
        Decimal(300),
        Decimal(95),
    )
    assert constraint.to_dict() == {
        "metric": "latency",
        "operator": "<=",
        "value": "300",
        "unit": "ms",
        "percentile": "95",
    }
    assert parse_structured_data(constraint.to_dict()) == constraint


def test_a_set_constraint_is_sorted_and_round_trips() -> None:
    constraint = parse_structured_data(
        {"metric": "regions", "operator": "in", "values": ["EU-West-1", "eu-central-1"]}
    )
    assert constraint == SetConstraint(metric="regions", values=("eu-central-1", "eu-west-1"))
    assert constraint is not None
    assert parse_structured_data(constraint.to_dict()) == constraint


@pytest.mark.parametrize(
    ("raw", "field", "reason"),
    [
        ([], "structured_data", "not_an_object"),
        ("2000 rps", "structured_data", "not_an_object"),
        ({"operator": ">=", "value": 1, "unit": "ms"}, "structured_data.metric", "required"),
        ({"metric": "latency", "value": 1, "unit": "ms"}, "structured_data.operator", "required"),
        (
            {"metric": "latency", "operator": "~", "value": 1, "unit": "ms"},
            "structured_data.operator",
            "unknown_operator",
        ),
        (
            {"metric": "latency", "operator": [], "value": 1, "unit": "ms"},
            "structured_data.operator",
            "unknown_operator",
        ),
        ({"metric": "latency", "operator": "<=", "unit": "ms"}, "structured_data.value", "required"),
        ({"metric": "latency", "operator": "<=", "value": 1}, "structured_data.unit", "required"),
        (
            {"metric": "latency", "operator": "<=", "value": 1, "unit": "ms", "code": "import os"},
            "structured_data.code",
            "unknown_field",
        ),
        (
            {"metric": "latency", "operator": "<=", "value": 1, "unit": "ms", "percentile": 101},
            "structured_data.percentile",
            "out_of_range",
        ),
        (
            {"metric": "latency", "operator": "<=", "value": 1, "unit": "ms", "percentile": 0},
            "structured_data.percentile",
            "out_of_range",
        ),
        ({"metric": "regions", "operator": "in", "values": []}, "structured_data.values", "length"),
        ({"metric": "regions", "operator": "in", "values": "eu-west-1"}, "structured_data.values", "length"),
        ({"metric": "regions", "operator": "in", "values": ["eu west"]}, "structured_data.values", "format"),
        (
            {"metric": "regions", "operator": "in", "values": ["a", "A"]},
            "structured_data.values",
            "duplicate",
        ),
        (
            {"metric": "regions", "operator": "in", "values": ["a"], "value": 1},
            "structured_data.value",
            "unknown_field",
        ),
    ],
)
def test_malformed_structured_data(raw: object, field: str, reason: str) -> None:
    with pytest.raises(InvalidRequirement) as error:
        parse_structured_data(raw)
    assert reason_of(error) == (field, reason)
