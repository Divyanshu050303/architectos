import itertools
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from core.domain.requirements.analysis import Concern, Issue, Severity, analyze, validate_requirement
from core.domain.requirements.entities import NewRequirement, Requirement
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementSource,
    RequirementStatus,
    RequirementType,
)

NOW = datetime(2026, 9, 25, tzinfo=UTC)
PROJECT = uuid.uuid7()
_numbers = itertools.count(1)
S = RequirementStatus
T = RequirementType


def req(
    type_: T = T.CAPACITY,
    category: str = "throughput",
    data: dict[str, Any] | None = None,
    status: S = S.ACTIVE,
    **overrides: Any,
) -> Requirement:
    created = NewRequirement.create(
        project_id=PROJECT,
        created_by_user_id=uuid.uuid7(),
        type=type_,
        category=category,
        title="A requirement",
        statement="Statement.",
        priority=RequirementPriority.HIGH,
        status=S.ACTIVE if status is S.ACTIVE else S.DRAFT,  # created valid, then moved
        structured_data=data or {},
        **overrides,
    )
    content = replace(created.content, status=status)
    return Requirement(
        id=uuid.uuid7(),
        project_id=PROJECT,
        number=next(_numbers),
        version=1,
        content=content,
        source=created.source,
        confidence=created.confidence,
        created_by_user_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def rps(operator: str, value: Any, unit: str = "requests/second") -> dict[str, Any]:
    return {"metric": "requests_per_second", "operator": operator, "value": value, "unit": unit}


def latency(operator: str, value: Any, percentile: Any = 95) -> dict[str, Any]:
    data = {"metric": "latency", "operator": operator, "value": value, "unit": "ms"}
    return data | ({"percentile": percentile} if percentile is not None else {})


# --- conflicts -----------------------------------------------------------------------------------


def test_the_specs_direct_conflict() -> None:
    at_least, at_most = req(data=rps(">=", 10_000)), req(data=rps("<=", 5_000))
    [conflict] = analyze([at_least, at_most]).conflicts
    assert (conflict.reason, conflict.metric, conflict.requirements) == (
        "disjoint_bounds",
        "requests_per_second",
        (at_least, at_most),
    )
    assert conflict.message == (
        f"{at_least.reference} requires requests_per_second >= 10000 requests/second, but "
        f"{at_most.reference} requires requests_per_second <= 5000 requests/second: no value satisfies both."
    )


@pytest.mark.parametrize(
    ("a", "b", "conflict"),
    [
        (rps(">=", 5000), rps("<=", 5000), False),  # exactly 5000 satisfies both
        (rps(">", 5000), rps("<=", 5000), True),
        (rps(">=", 5000), rps("<", 5000), True),
        (rps(">=", 1000), rps(">=", 5000), False),  # two floors never conflict
        (rps("<=", 1000), rps("<=", 5000), False),
        (rps(">=", 600_000, "requests/minute"), rps("<=", 5000), True),  # 10000/s vs 5000/s: units compared
        (rps(">=", 300_000, "requests/minute"), rps("<=", 5000), False),  # exactly 5000/s
    ],
)
def test_interval_rules(a: dict[str, Any], b: dict[str, Any], conflict: bool) -> None:
    assert bool(analyze([req(data=a), req(data=b)]).conflicts) is conflict


def test_tighter_bounds_in_the_same_direction_are_not_conflicts() -> None:
    """The spec: latency <= 300 ms and <= 100 ms are not necessarily conflicts."""
    requirements = [
        req(T.PERFORMANCE, "latency", latency("<=", 300)),
        req(T.PERFORMANCE, "latency", latency("<=", 100)),
    ]
    assert analyze(requirements).conflicts == ()


def test_only_like_is_compared_with_like() -> None:
    p95, p99 = latency("<=", 100, 95), latency("<=", 50, 99)
    assert analyze([req(T.PERFORMANCE, "latency", p95), req(T.PERFORMANCE, "latency", p99)]).conflicts == ()
    usd = {"metric": "monthly_budget", "operator": "<=", "value": 100, "unit": "USD/month"}
    eur = {"metric": "monthly_budget", "operator": "<=", "value": 50, "unit": "EUR/month"}
    assert analyze([req(T.COST, "budget", usd), req(T.COST, "budget", eur)]).conflicts == ()


def test_tensions_are_not_conflicts() -> None:
    availability = {"metric": "availability", "operator": ">=", "value": "99.99", "unit": "%"}
    budget = {"metric": "monthly_budget", "operator": "<=", "value": 100, "unit": "USD/month"}
    assert (
        analyze([req(T.AVAILABILITY, "availability", availability), req(T.COST, "budget", budget)]).conflicts
        == ()
    )


def test_disjoint_region_sets_conflict() -> None:
    eu = req(
        T.OPERATIONAL,
        "regions",
        {"metric": "regions", "operator": "in", "values": ["eu-west-1", "eu-central-1"]},
    )
    us = req(T.OPERATIONAL, "regions", {"metric": "regions", "operator": "in", "values": ["us-east-1"]})
    both = req(
        T.COMPLIANCE,
        "data_residency",
        {"metric": "regions", "operator": "in", "values": ["eu-west-1", "us-east-1"]},
    )
    conflicts = analyze([eu, us, both]).conflicts
    assert [(c.reason, c.requirements) for c in conflicts] == [("disjoint_sets", (eu, us))]


def test_invalid_and_deprecated_requirements_are_out_of_play() -> None:
    floor = req(data=rps(">=", 10_000))
    retired = req(data=rps("<=", 5_000), status=S.DEPRECATED)
    rejected = req(data=rps("<=", 4_000), status=S.INVALID)
    analysis = analyze([floor, retired, rejected])
    assert analysis.conflicts == ()
    assert analysis.requirements == (floor,)


def test_drafts_are_in_play() -> None:
    assert analyze([req(data=rps(">=", 10_000)), req(data=rps("<=", 5_000), status=S.DRAFT)]).conflicts


# --- completeness, ambiguity, unbounded ----------------------------------------------------------


def test_completeness_names_covered_and_missing_concerns() -> None:
    traffic = req(data=rps(">=", 2000))
    speed = req(T.PERFORMANCE, "latency", latency("<=", 300))
    analysis = analyze([traffic, speed])
    assert [(c.concern, c.requirements) for c in analysis.covered] == [
        (Concern.TRAFFIC, (traffic,)),
        (Concern.LATENCY, (speed,)),
    ]
    assert analysis.missing == (Concern.AVAILABILITY, Concern.DATA, Concern.SECURITY, Concern.RETENTION)


def test_a_complete_project() -> None:
    requirements = [
        req(data=rps(">=", 2000)),
        req(T.PERFORMANCE, "latency", latency("<=", 300)),
        req(
            T.AVAILABILITY,
            "availability",
            {"metric": "availability", "operator": ">=", "value": "99.9", "unit": "%"},
        ),
        req(T.DATA, "retention", {"metric": "retention", "operator": ">=", "value": 30, "unit": "d"}),
        req(T.SECURITY, "encryption"),
    ]
    analysis = analyze(requirements)
    assert analysis.missing == ()
    assert (analysis.conflicts, analysis.ambiguous, analysis.unbounded) == ((), (), ())


def test_ambiguous_requirements() -> None:
    no_numbers = req(status=S.DRAFT)
    no_percentile = req(T.PERFORMANCE, "latency", latency("<=", 300, None))
    unsure = req(data=rps(">=", 10), status=S.DRAFT, source=RequirementSource.AI, confidence="0.5")
    sure = req(data=rps(">=", 20), status=S.DRAFT, source=RequirementSource.AI, confidence="0.9")
    reasons = [
        (a.requirement, a.reason) for a in analyze([no_numbers, no_percentile, unsure, sure]).ambiguous
    ]
    assert reasons == [
        (no_numbers, "missing_constraint"),
        (no_percentile, "missing_percentile"),
        (unsure, "low_confidence"),
    ]


def test_sizing_metrics_with_only_ceilings_are_unbounded() -> None:
    ceiling = req(data=rps("<=", 5000))
    [unbounded] = analyze([ceiling]).unbounded
    assert (unbounded.metric, unbounded.requirements, unbounded.reason) == (
        "requests_per_second",
        (ceiling,),
        "no_lower_bound",
    )
    assert analyze([ceiling, req(data=rps(">=", 1000))]).unbounded == ()


def test_analysis_is_deterministic_and_ordered_by_number() -> None:
    requirements = [req(data=rps(">=", 10_000)), req(data=rps("<=", 5_000)), req(data=rps("<=", 4_000))]
    forward, backward = analyze(requirements), analyze(list(reversed(requirements)))
    assert forward == backward
    assert [c.requirements for c in forward.conflicts] == [
        (requirements[0], requirements[1]),
        (requirements[0], requirements[2]),
    ]


# --- validation reports --------------------------------------------------------------------------


def test_a_valid_active_requirement() -> None:
    report = validate_requirement(req(data=rps(">=", 2000)))
    assert (report.valid, report.ready_for_active, report.issues) == (True, True, ())


def test_a_draft_that_is_not_ready() -> None:
    report = validate_requirement(req(status=S.DRAFT))
    assert (report.valid, report.ready_for_active) == (True, False)
    assert [(i.severity, i.field, i.reason) for i in report.issues] == [
        (Severity.WARNING, "structured_data", "missing_constraint"),
        (Severity.WARNING, "structured_data", "not_ready_for_active"),
    ]


def test_history_written_under_older_rules_is_reported() -> None:
    current = req(data=rps(">=", 2000))
    legacy = replace(current, content=replace(current.content, category="legacy"))
    report = validate_requirement(legacy)
    assert not report.valid
    assert report.issues[0] == Issue(Severity.ERROR, "category", "unknown_for_type")
