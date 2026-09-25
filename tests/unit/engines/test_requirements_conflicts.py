"""Conflict and consistency detection (Requirements Engine phase 7)."""

import uuid
from typing import Any

import pytest

from core.domain.requirements.analysis import Severity
from core.domain.requirements.entities import NewRequirement
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementScope,
    RequirementStatus,
    RequirementType,
)
from engines.requirements.conflicts import Existing, find_conflicts
from engines.requirements.extractor import extract
from engines.requirements.findings import Finding, FindingKind
from engines.requirements.validation import validate


def findings(text: str, *existing: Existing) -> list[Finding]:
    return find_conflicts(validate(extract(text)).candidates, existing)


def summary(text: str, *existing: Existing) -> list[tuple[str, str, str]]:
    return [(f.kind.value, f.code, f.severity.value) for f in findings(text, *existing)]


def existing(reference: str, **overrides: Any) -> Existing:
    fields: dict[str, Any] = {
        "project_id": uuid.uuid7(),
        "created_by_user_id": uuid.uuid7(),
        "type": RequirementType.PERFORMANCE,
        "category": "latency",
        "title": "Latency",
        "statement": "Existing.",
        "priority": RequirementPriority.HIGH,
        "status": RequirementStatus.ACTIVE,
        "structured_data": {
            "metric": "latency",
            "operator": "<=",
            "value": 300,
            "unit": "ms",
            "percentile": 95,
        },
    }
    content = NewRequirement.create(**(fields | overrides)).content
    return Existing(reference, 2, content)


# --- the spec's cases ------------------------------------------------------------------------------


def test_contradictory_bounds_are_a_blocking_conflict() -> None:
    [finding] = findings("Support at least 10,000 rps. Support at most 5,000 rps.")
    assert (finding.kind, finding.code, finding.severity, finding.metric) == (
        FindingKind.CONFLICT,
        "disjoint_bounds",
        Severity.BLOCKING,
        "requests_per_second",
    )
    assert finding.message == (
        "“at least 10,000 rps” requires requests_per_second >= 10000 requests/second, but "
        "“at most 5,000 rps” requires requests_per_second <= 5000 requests/second: no value satisfies both."
    )
    assert len(finding.candidate_keys) == 2
    assert finding.key.startswith("find_")
    assert finding.suggestion


def test_a_stricter_latency_is_not_a_conflict() -> None:
    assert summary("p95 latency under 300 ms. p95 latency under 100 ms.") == [
        ("consistency", "strengthens", "info")
    ]
    [finding] = findings("p95 latency under 300 ms. p95 latency under 100 ms.")
    assert (
        finding.message
        == "“under 100 ms” (< 100 ms) is stricter than “under 300 ms” (< 300 ms), so it supersedes it."
    )


def test_a_stricter_availability_is_not_a_contradiction() -> None:
    assert summary("At least 99.9% availability. At least 99.99% availability.") == [
        ("consistency", "strengthens", "info")
    ]


# --- more relations --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "A target of exactly 300 ms latency at p95. p95 latency under 200 ms.",
            [("conflict", "disjoint_bounds", "blocking")],
        ),
        (
            "Storage between 10 and 20 GB. At least 25 GB of storage.",
            [("conflict", "disjoint_bounds", "blocking")],
        ),
        (
            "Restore within 2 minutes. Recovery time within 120 seconds.",
            [("consistency", "equivalent", "info")],
        ),
        ("At least 100 rps. At most 500 rps.", []),  # together: a range
        ("API p95 latency under 100 ms. Database p95 latency of exactly 200 ms.", []),  # different scopes
        ("p95 latency under 100 ms. p99 latency of exactly 200 ms.", []),  # different percentiles
        ("99.99% availability. Spend at most 100 EUR/month.", []),  # a tension, not a conflict
        (
            "At least 600,000 requests per minute. At most 5,000 rps.",
            [("conflict", "disjoint_bounds", "blocking")],
        ),
    ],
)
def test_relations(text: str, expected: list[tuple[str, str, str]]) -> None:
    assert summary(text) == expected


# --- against the project's existing requirements ---------------------------------------------------


def test_a_candidate_contradicting_an_existing_requirement() -> None:
    [finding] = findings("p95 latency of exactly 500 ms.", existing("REQ-3"))
    assert (finding.code, finding.severity) == ("disjoint_bounds", Severity.BLOCKING)
    assert finding.requirement_references == ("REQ-3@v2",)
    assert len(finding.candidate_keys) == 1
    assert "REQ-3 (Latency)" in finding.message


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("p95 latency under 100 ms.", "tightens_existing"),
        ("p95 latency under 800 ms.", "already_covered"),
        ("p95 latency at most 300 ms.", "already_required"),
    ],
)
def test_candidates_relative_to_existing_requirements(text: str, code: str) -> None:
    [finding] = findings(text, existing("REQ-3"))
    assert (finding.kind, finding.code, finding.severity) == (FindingKind.CONSISTENCY, code, Severity.INFO)
    assert finding.requirement_references == ("REQ-3@v2",)
    assert "REQ-3" in (finding.suggestion or "")


def test_disjoint_region_sets() -> None:
    eu_only = existing(
        "REQ-7",
        type=RequirementType.OPERATIONAL,
        category="regions",
        title="Regions",
        structured_data={"metric": "regions", "operator": "in", "values": ["eu-west-1"]},
    )
    [finding] = findings("Deploy to us-east-1.", eu_only)
    assert (finding.code, finding.severity) == ("disjoint_sets", Severity.BLOCKING)
    assert "allow no regions in common" in finding.message


def test_existing_requirements_among_themselves_are_not_re_reported() -> None:
    first = existing(
        "REQ-1",
        structured_data={"metric": "latency", "operator": "<=", "value": 100, "unit": "ms", "percentile": 95},
    )
    second = existing(
        "REQ-2",
        structured_data={"metric": "latency", "operator": "==", "value": 500, "unit": "ms", "percentile": 95},
    )
    assert find_conflicts((), (first, second)) == []


def test_scope_is_respected_against_existing_requirements() -> None:
    api = existing("REQ-3", scope=RequirementScope.API)
    assert findings("Database p95 latency of exactly 500 ms.", api) == []


def test_conflict_detection_is_deterministic() -> None:
    text = "At least 10,000 rps. At most 5,000 rps. p95 latency under 300 ms. p95 latency under 100 ms."
    first, second = findings(text), findings(text)
    assert first == second
    assert [f.kind for f in first] == [FindingKind.CONFLICT, FindingKind.CONSISTENCY]
