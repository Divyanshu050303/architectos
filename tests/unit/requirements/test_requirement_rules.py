from collections.abc import Mapping
from decimal import Decimal

import pytest

from core.domain.requirements.enums import RequirementStatus, RequirementType
from core.domain.requirements.errors import InvalidRequirement, InvalidStatusTransition
from core.domain.requirements.requirements import (
    CLOSED_CATEGORY_TYPES,
    KNOWN_CATEGORIES,
    MEASURABLE_TYPES,
    METRICS,
    TRANSITIONS,
    check_transition,
    content_locked,
    validate_content,
)
from core.domain.requirements.value_objects import CANONICAL_UNITS, Dimension, parse_structured_data

T = RequirementType
S = RequirementStatus


def validate(type_: T, category: str, raw: Mapping[str, object], status: S = S.ACTIVE) -> None:
    validate_content(type_, category, status, parse_structured_data(dict(raw)))


def rejection(type_: T, category: str, raw: Mapping[str, object], status: S = S.ACTIVE) -> tuple[str, str]:
    with pytest.raises(InvalidRequirement) as error:
        validate(type_, category, raw, status)
    return error.value.details["field"], error.value.details["reason"]


# --- taxonomy invariants -------------------------------------------------------------------------


def test_every_type_has_known_categories() -> None:
    assert set(KNOWN_CATEGORIES) == set(RequirementType)
    assert MEASURABLE_TYPES <= CLOSED_CATEGORY_TYPES


def test_every_metric_is_reachable_from_each_of_its_types() -> None:
    for name, rule in METRICS.items():
        for type_ in rule.types:
            assert rule.categories & KNOWN_CATEGORIES[type_], (name, type_)
        if rule.dimension is not None:
            assert rule.operators, name


def test_every_measurable_category_has_a_metric() -> None:
    """Otherwise an in-force requirement in that category could never be valid."""
    for type_ in MEASURABLE_TYPES:
        for category in KNOWN_CATEGORIES[type_]:
            assert any(type_ in r.types and category in r.categories for r in METRICS.values()), (
                type_,
                category,
            )


def test_every_quantity_metric_has_a_canonical_unit_or_is_money() -> None:
    for name, rule in METRICS.items():
        if rule.dimension is not None:
            assert rule.dimension in CANONICAL_UNITS or rule.dimension is Dimension.MONEY_PER_MONTH, name


# --- the spec's examples -------------------------------------------------------------------------


def test_spec_examples_are_valid() -> None:
    validate(
        T.CAPACITY,
        "throughput",
        {"metric": "requests_per_second", "operator": ">=", "value": 2000, "unit": "requests/second"},
    )
    validate(
        T.PERFORMANCE,
        "latency",
        {"metric": "latency", "operator": "<=", "value": 300, "unit": "ms", "percentile": 95},
    )


@pytest.mark.parametrize(
    ("type_", "category", "raw"),
    [
        (T.PERFORMANCE, "latency", {"metric": "latency", "operator": "<=", "value": -100, "unit": "ms"}),
        (
            T.AVAILABILITY,
            "availability",
            {"metric": "availability", "operator": ">=", "value": 150, "unit": "%"},
        ),
        (
            T.CAPACITY,
            "throughput",
            {"metric": "requests_per_second", "operator": ">=", "value": -50, "unit": "requests/second"},
        ),
        (
            T.CAPACITY,
            "throughput",
            {"metric": "requests_per_second", "operator": ">=", "value": 0, "unit": "requests/second"},
        ),
        (T.PERFORMANCE, "latency", {"metric": "latency", "operator": "<=", "value": 0, "unit": "ms"}),
        (
            T.RELIABILITY,
            "availability",
            {"metric": "availability", "operator": ">=", "value": 0, "unit": "ratio"},
        ),
        (T.DATA, "storage", {"metric": "storage", "operator": "<=", "value": 0, "unit": "GB"}),
        (T.COST, "budget", {"metric": "monthly_budget", "operator": "<=", "value": -1, "unit": "USD/month"}),
    ],
    ids=[
        "negative-latency",
        "availability-150",
        "negative-rps",
        "zero-rps",
        "zero-latency",
        "zero-availability",
        "zero-storage",
        "negative-budget",
    ],
)
def test_impossible_values_are_out_of_range(type_: T, category: str, raw: Mapping[str, object]) -> None:
    assert rejection(type_, category, raw) == ("structured_data.value", "out_of_range")


def test_boundaries_that_are_meaningful_are_allowed() -> None:
    validate(
        T.AVAILABILITY,
        "availability",
        {"metric": "availability", "operator": ">=", "value": 100, "unit": "%"},
    )
    validate(T.RELIABILITY, "rpo", {"metric": "rpo", "operator": "<=", "value": 0, "unit": "s"})
    validate(
        T.COST, "budget", {"metric": "monthly_budget", "operator": "<=", "value": 0, "unit": "USD/month"}
    )


@pytest.mark.parametrize(
    ("type_", "category", "raw"),
    [
        (T.RELIABILITY, "rpo", {"metric": "rpo", "operator": "<", "value": 0, "unit": "s"}),
        (
            T.AVAILABILITY,
            "availability",
            {"metric": "availability", "operator": ">", "value": 100, "unit": "%"},
        ),
    ],
)
def test_bounds_nothing_can_satisfy(type_: T, category: str, raw: Mapping[str, object]) -> None:
    assert rejection(type_, category, raw) == ("structured_data.value", "unsatisfiable")


def test_user_counts_are_whole_numbers() -> None:
    raw = {"metric": "concurrent_users", "operator": ">=", "value": "1.5", "unit": "users"}
    assert rejection(T.CAPACITY, "concurrent_users", raw) == ("structured_data.value", "not_integral")


@pytest.mark.parametrize(
    ("type_", "category", "raw", "expected"),
    [
        (
            T.CAPACITY,
            "throughput",
            {"metric": "rps", "operator": ">=", "value": 1, "unit": "requests/second"},
            ("structured_data.metric", "unknown_metric"),
        ),
        (
            T.SECURITY,
            "encryption",
            {"metric": "latency", "operator": "<=", "value": 1, "unit": "ms"},
            ("structured_data.metric", "not_allowed_for_type"),
        ),
        (
            T.PERFORMANCE,
            "throughput",
            {"metric": "latency", "operator": "<=", "value": 1, "unit": "ms"},
            ("structured_data.metric", "not_allowed_for_category"),
        ),
        (
            T.PERFORMANCE,
            "latency",
            {"metric": "latency", "operator": ">=", "value": 1, "unit": "ms"},
            ("structured_data.operator", "not_allowed_for_metric"),
        ),
        (
            T.PERFORMANCE,
            "latency",
            {"metric": "latency", "operator": "<=", "value": 1, "unit": "%"},
            ("structured_data.unit", "not_allowed_for_metric"),
        ),
        (
            T.CAPACITY,
            "throughput",
            {
                "metric": "requests_per_second",
                "operator": ">=",
                "value": 1,
                "unit": "requests/second",
                "percentile": 99,
            },
            ("structured_data.percentile", "not_allowed_for_metric"),
        ),
        (
            T.OPERATIONAL,
            "regions",
            {"metric": "regions", "operator": ">=", "value": 1, "unit": "users"},
            ("structured_data.operator", "not_allowed_for_metric"),
        ),
        (
            T.PERFORMANCE,
            "latency",
            {"metric": "latency", "operator": "in", "values": ["fast"]},
            ("structured_data.operator", "not_allowed_for_metric"),
        ),
    ],
    ids=[
        "unknown-metric",
        "metric-wrong-type",
        "metric-wrong-category",
        "latency-lower-bound",
        "latency-in-percent",
        "percentile-on-rps",
        "regions-as-quantity",
        "latency-as-set",
    ],
)
def test_metric_rules(type_: T, category: str, raw: Mapping[str, object], expected: tuple[str, str]) -> None:
    assert rejection(type_, category, raw) == expected


def test_regions_are_a_set_constraint() -> None:
    validate(T.OPERATIONAL, "regions", {"metric": "regions", "operator": "in", "values": ["eu-west-1"]})


# --- categories ----------------------------------------------------------------------------------


def test_closed_types_need_a_known_category() -> None:
    assert rejection(T.CAPACITY, "vibes", {}, S.DRAFT) == ("category", "unknown_for_type")


def test_open_types_accept_any_well_formed_category() -> None:
    validate(T.FUNCTIONAL, "loyalty_points", {})
    validate(T.SECURITY, "rate_limiting", {})


# --- completeness in force -----------------------------------------------------------------------


@pytest.mark.parametrize("status", [S.ACTIVE, S.SATISFIED])
def test_measurable_requirements_in_force_need_a_constraint(status: S) -> None:
    assert rejection(T.CAPACITY, "throughput", {}, status) == ("structured_data", "required_when_in_force")


@pytest.mark.parametrize("status", [S.DRAFT, S.INVALID, S.DEPRECATED])
def test_other_statuses_may_be_incomplete(status: S) -> None:
    validate(T.CAPACITY, "throughput", {}, status)


def test_qualitative_requirements_need_no_constraint() -> None:
    validate(T.FUNCTIONAL, "order", {})
    validate(T.DATA, "consistency", {})


# --- lifecycle -----------------------------------------------------------------------------------

ALLOWED = {
    (S.DRAFT, S.ACTIVE),
    (S.DRAFT, S.DEPRECATED),
    (S.ACTIVE, S.SATISFIED),
    (S.ACTIVE, S.INVALID),
    (S.ACTIVE, S.DEPRECATED),
    (S.SATISFIED, S.ACTIVE),
    (S.SATISFIED, S.DEPRECATED),
    (S.INVALID, S.DRAFT),
    (S.INVALID, S.DEPRECATED),
}


@pytest.mark.parametrize("current", list(S))
@pytest.mark.parametrize("target", list(S))
def test_status_transitions(current: S, target: S) -> None:
    if current is target or (current, target) in ALLOWED:
        check_transition(current, target)
    else:
        with pytest.raises(InvalidStatusTransition) as error:
            check_transition(current, target)
        assert error.value.details == {"from": current.value, "to": target.value}


def test_deprecated_is_final() -> None:
    assert TRANSITIONS[S.DEPRECATED] == frozenset()


def test_content_locks() -> None:
    assert content_locked(S.DEPRECATED, S.DEPRECATED)
    assert content_locked(S.SATISFIED, S.SATISFIED)
    assert not content_locked(S.SATISFIED, S.ACTIVE)  # reopening while editing
    assert not any(content_locked(s, s) for s in (S.DRAFT, S.ACTIVE, S.INVALID))


def test_bounds_are_checked_in_the_canonical_unit() -> None:
    # 100 % is the ratio 1 (the maximum), 100.1 % is above it.
    validate(
        T.AVAILABILITY,
        "availability",
        {"metric": "availability", "operator": ">=", "value": "1", "unit": "ratio"},
    )
    raw = {"metric": "availability", "operator": ">=", "value": "100.1", "unit": "%"}
    assert rejection(T.AVAILABILITY, "availability", raw) == ("structured_data.value", "out_of_range")
    assert METRICS["availability"].maximum == Decimal(1)
