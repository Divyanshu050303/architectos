"""Phase 1 of the Requirements Engine: scope, targets (==) and ranges, the new sources and taxonomy."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest

from core.domain.requirements.analysis import Severity, analyze, find_conflicts
from core.domain.requirements.entities import NewRequirement, Requirement, RequirementChanges
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementScope,
    RequirementSource,
    RequirementStatus,
    RequirementType,
)
from core.domain.requirements.errors import InvalidRequirement
from core.domain.requirements.normalization import (
    Interval,
    canonical,
    canonical_data,
    normalize_structured_data,
)
from core.domain.requirements.requirements import INITIAL_STATUSES
from core.domain.requirements.value_objects import (
    Operator,
    QuantityConstraint,
    RangeConstraint,
    parse_structured_data,
)

T = RequirementType
S = RequirementStatus
NOW = datetime(2026, 9, 26, tzinfo=UTC)
PROJECT = uuid.uuid7()


def quantity(raw: dict[str, Any]) -> QuantityConstraint | RangeConstraint:
    parsed = parse_structured_data(raw)
    assert isinstance(parsed, QuantityConstraint | RangeConstraint)
    return parsed


def new(**overrides: Any) -> NewRequirement:
    fields: dict[str, Any] = {
        "project_id": PROJECT,
        "created_by_user_id": uuid.uuid7(),
        "type": T.CAPACITY,
        "category": "throughput",
        "title": "Throughput",
        "statement": "Statement.",
        "priority": RequirementPriority.HIGH,
        "status": S.ACTIVE,
        "structured_data": {"metric": "requests_per_second", "operator": ">=", "value": 2000, "unit": "rps"},
    }
    return NewRequirement.create(**(fields | overrides))


_numbers = iter(range(1, 10_000))


def stored(**overrides: Any) -> Requirement:
    created = new(**overrides)
    return Requirement(
        id=uuid.uuid7(),
        project_id=PROJECT,
        number=next(_numbers),
        version=1,
        content=created.content,
        source=created.source,
        confidence=created.confidence,
        created_by_user_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def latency(
    operator: str, value: Any, scope: RequirementScope = RequirementScope.SYSTEM, **extra: Any
) -> Requirement:
    data = {"metric": "latency", "operator": operator, "unit": "ms", "percentile": 95} | extra
    if value is not None:
        data["value"] = value
    return stored(type=T.PERFORMANCE, category="latency", structured_data=data, scope=scope)


# --- scope -----------------------------------------------------------------------------------------


def test_scope_defaults_to_system_and_is_never_invented() -> None:
    assert new().content.scope is RequirementScope.SYSTEM
    assert new(scope=RequirementScope.API).content.scope is RequirementScope.API


def test_changing_the_scope_is_a_new_version() -> None:
    requirement = stored()
    revision = requirement.revise(
        expected_version=1,
        changes=RequirementChanges(scope=RequirementScope.API),
        change_reason="It is the public API",
        author_user_id=uuid.uuid7(),
    )
    assert revision is not None
    assert (revision.requirement.version, revision.requirement.content.scope) == (2, RequirementScope.API)


def test_requirements_on_different_scopes_never_conflict() -> None:
    api = latency("<=", 100, RequirementScope.API)
    database = latency("==", 200, RequirementScope.DATABASE)
    assert find_conflicts([api, database]) == []
    assert find_conflicts([api, latency("==", 200, RequirementScope.API)])


# --- targets and ranges ----------------------------------------------------------------------------


def test_a_target_and_a_range_parse_and_round_trip() -> None:
    target = parse_structured_data({"metric": "availability", "operator": "==", "value": "99.9", "unit": "%"})
    assert target is not None
    assert target.operator is Operator.EQUALS
    assert parse_structured_data(target.to_dict()) == target

    storage = parse_structured_data(
        {"metric": "storage", "operator": "between", "min": 10, "max": 20, "unit": "GB"}
    )
    assert isinstance(storage, RangeConstraint)
    assert storage.to_dict() == {
        "metric": "storage",
        "operator": "between",
        "min": "10",
        "max": "20",
        "unit": "GB",
    }
    assert parse_structured_data(storage.to_dict()) == storage
    assert canonical_data(storage) == {
        "metric": "storage",
        "operator": "between",
        "min": "10000000000",
        "max": "20000000000",
        "unit": "B",
    }


@pytest.mark.parametrize(
    ("raw", "field", "reason"),
    [
        ({"operator": "between", "min": 20, "max": 10, "unit": "GB"}, "structured_data.max", "not_above_min"),
        ({"operator": "between", "min": 10, "max": 10, "unit": "GB"}, "structured_data.max", "not_above_min"),
        ({"operator": "between", "max": 10, "unit": "GB"}, "structured_data.min", "required"),
        (
            {"operator": "between", "min": 1, "max": 2, "value": 3, "unit": "GB"},
            "structured_data.value",
            "unknown_field",
        ),
    ],
)
def test_malformed_ranges(raw: dict[str, Any], field: str, reason: str) -> None:
    with pytest.raises(InvalidRequirement) as error:
        parse_structured_data({"metric": "storage"} | raw)
    assert error.value.details == {"field": field, "reason": reason}


def test_ranges_only_where_both_directions_make_sense_and_within_bounds() -> None:
    with pytest.raises(InvalidRequirement) as error:
        latency("between", None, min=100, max=300)
    assert error.value.details == {"field": "structured_data.operator", "reason": "not_allowed_for_metric"}
    with pytest.raises(InvalidRequirement) as error:
        new(
            structured_data={
                "metric": "requests_per_second",
                "operator": "between",
                "min": -5,
                "max": 10,
                "unit": "rps",
            }
        )
    assert error.value.details == {"field": "structured_data.min", "reason": "out_of_range"}
    with pytest.raises(InvalidRequirement) as error:
        new(
            category="concurrent_users",
            structured_data={
                "metric": "concurrent_users",
                "operator": "between",
                "min": 1,
                "max": "2.5",
                "unit": "users",
            },
        )
    assert error.value.details == {"field": "structured_data.max", "reason": "not_integral"}


def test_every_quantity_accepts_a_target() -> None:
    new(
        type=T.AVAILABILITY,
        category="availability",
        structured_data={"metric": "availability", "operator": "==", "value": "99.9", "unit": "%"},
    )
    latency("==", 300)


@pytest.mark.parametrize(
    ("alias", "operator"), [("=", "=="), ("EQ", "=="), ("gte", ">="), ("lt", "<"), ("Range", "between")]
)
def test_operator_spellings(alias: str, operator: str) -> None:
    normalized = normalize_structured_data({"metric": "latency", "operator": alias, "value": 1, "unit": "ms"})
    assert isinstance(normalized, dict)
    assert normalized["operator"] == operator


def test_range_values_are_normalized_like_values() -> None:
    normalized = normalize_structured_data(
        {"metric": "rps", "operator": "range", "min": "1k", "max": "2,500", "unit": "rps"}
    )
    assert normalized == {
        "metric": "requests_per_second",
        "operator": "between",
        "min": "1000",
        "max": "2500",
        "unit": "requests/second",
    }


# --- intervals and conflicts -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "conflict"),
    [
        (("==", 300), ("<=", 200), True),
        (("==", 300), ("<=", 300), False),
        (("==", 300), ("<", 300), True),
        (("==", 300), ("==", 300), False),
        (("==", 300), ("==", 301), True),
        (("between", (100, 200)), ("<", 100), True),
        (("between", (100, 200)), ("<=", 100), False),
        (("between", (100, 200)), ("between", (200, 300)), False),
        (("between", (100, 200)), ("between", (201, 300)), True),
    ],
)
def test_interval_conflicts(a: tuple[str, Any], b: tuple[str, Any], conflict: bool) -> None:
    def requirement(spec: tuple[str, Any]) -> Requirement:
        operator, value = spec
        if operator == "between":
            return stored(
                structured_data={
                    "metric": "requests_per_second",
                    "operator": "between",
                    "min": value[0],
                    "max": value[1],
                    "unit": "rps",
                }
            )
        return stored(
            structured_data={
                "metric": "requests_per_second",
                "operator": operator,
                "value": value,
                "unit": "rps",
            }
        )

    found = find_conflicts([requirement(a), requirement(b)])
    assert bool(found) is conflict
    if found:
        assert (found[0].severity, found[0].key.startswith("disjoint_bounds:REQ-")) == (
            Severity.BLOCKING,
            True,
        )


def test_conflict_messages_describe_ranges() -> None:
    first = stored(
        structured_data={
            "metric": "requests_per_second",
            "operator": "between",
            "min": 1,
            "max": 2,
            "unit": "requests/minute",
        }
    )
    second = stored(
        structured_data={"metric": "requests_per_second", "operator": ">=", "value": 60, "unit": "rps"}
    )
    [conflict] = find_conflicts([first, second])
    assert "between 0.016666667 and 0.033333333 requests/second" in conflict.message


def test_interval_containment_means_strengthening() -> None:
    at_most_300 = Interval(None, False, Fraction(300), True)
    at_most_100 = Interval(None, False, Fraction(100), True)
    assert at_most_300.contains(at_most_100)
    assert not at_most_100.contains(at_most_300)
    assert at_most_300.intersects(at_most_100)
    exactly = canonical(quantity({"metric": "latency", "operator": "==", "value": 50, "unit": "ms"})).interval
    assert at_most_100.contains(exactly)


def test_targets_and_ranges_count_as_floors_for_sizing() -> None:
    ranged = stored(
        structured_data={
            "metric": "requests_per_second",
            "operator": "between",
            "min": 1,
            "max": 5,
            "unit": "rps",
        }
    )
    assert analyze([ranged]).unbounded == ()


# --- sources ---------------------------------------------------------------------------------------


def test_only_a_person_starts_a_requirement_active() -> None:
    assert INITIAL_STATUSES[RequirementSource.USER] == frozenset({S.DRAFT, S.ACTIVE})
    for source in set(RequirementSource) - {RequirementSource.USER}:
        assert INITIAL_STATUSES[source] == frozenset({S.DRAFT}), source


@pytest.mark.parametrize(
    ("source", "confidence", "reason"),
    [
        (RequirementSource.AI, None, "required_for_ai"),
        (RequirementSource.DISCOVERY, None, "required_for_discovery"),
        (RequirementSource.USER, "0.9", "not_for_user"),
    ],
)
def test_confidence_by_source(source: RequirementSource, confidence: str | None, reason: str) -> None:
    status = S.ACTIVE if source is RequirementSource.USER else S.DRAFT
    with pytest.raises(InvalidRequirement) as error:
        new(source=source, status=status, confidence=confidence)
    assert error.value.details == {"field": "confidence", "reason": reason}


def test_system_and_imported_requirements_may_state_a_confidence() -> None:
    assert new(source=RequirementSource.SYSTEM, status=S.DRAFT, confidence="0.8").confidence is not None
    assert new(source=RequirementSource.IMPORTED, status=S.DRAFT).confidence is None


# --- taxonomy additions ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_", "category", "data"),
    [
        (T.CAPACITY, "monthly_active_users", {"metric": "mau", "operator": ">=", "quantity": "10M users"}),
        (
            T.CAPACITY,
            "orders_per_second",
            {"metric": "orders_per_second", "operator": ">=", "quantity": "500 orders/sec"},
        ),
        (
            T.PERFORMANCE,
            "throughput",
            {"metric": "ops", "operator": ">=", "value": "30k", "unit": "orders per minute"},
        ),
        (
            T.AVAILABILITY,
            "uptime",
            {"metric": "availability", "operator": ">=", "value": "99.95", "unit": "percentage"},
        ),
        (
            T.DATA,
            "data_volume",
            {"metric": "storage", "operator": "between", "min": 1, "max": 5, "unit": "TB"},
        ),
        (
            T.COST,
            "infrastructure_budget",
            {"metric": "monthly_budget", "operator": "<=", "value": 5000, "unit": "USD/month"},
        ),
        (
            T.COST,
            "monthly_budget",
            {"metric": "monthly_budget", "operator": "<=", "value": 100, "unit": "EUR/month"},
        ),
        (T.SECURITY, "secrets", {}),
        (T.FUNCTIONAL, "authentication", {}),
    ],
)
def test_new_taxonomy(type_: T, category: str, data: dict[str, Any]) -> None:
    new(type=type_, category=category, structured_data=data)


def test_orders_are_not_requests() -> None:
    with pytest.raises(InvalidRequirement) as error:
        new(
            structured_data={
                "metric": "requests_per_second",
                "operator": ">=",
                "value": 500,
                "unit": "orders/second",
            }
        )
    assert error.value.details == {"field": "structured_data.unit", "reason": "not_allowed_for_metric"}
    orders = canonical(
        quantity({"metric": "orders_per_second", "operator": ">=", "value": 60, "unit": "orders/minute"})
    )
    assert (orders.value, orders.unit) == (Decimal(1), "orders/second")


def test_severities() -> None:
    assert [s.value for s in Severity] == ["blocking", "warning", "info"]


def test_content_keeps_scope_through_replace() -> None:
    content = new(scope=RequirementScope.QUEUE).content
    assert replace(content, title="x").scope is RequirementScope.QUEUE
