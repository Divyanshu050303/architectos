"""Contextual completeness (Requirements Engine phase 8)."""

import uuid

import pytest

from core.domain.requirements.analysis import Severity
from core.domain.requirements.entities import NewRequirement
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from engines.requirements.completeness import (
    NOTHING_TO_ASSESS,
    PROFILES,
    Area,
    Completeness,
    CompletenessStatus,
    Importance,
    assess,
)
from engines.requirements.extractor import extract
from engines.requirements.validation import validate

from . import scenarios


def completeness(text: str) -> Completeness:
    validated = validate(extract(text))
    return assess(text, [c.content for c in validated.candidates])


def missing(text: str) -> dict[str, str]:
    return {f.code.removeprefix("missing_"): f.severity.value for f in completeness(text).findings}


# --- minimal, partial, well-specified --------------------------------------------------------------


def test_nothing_to_assess() -> None:
    for text in ("", "Hello there."):
        result = completeness(text)
        assert result.status is CompletenessStatus.UNKNOWN
        assert result.findings == (NOTHING_TO_ASSESS,)
        assert NOTHING_TO_ASSESS.severity is Severity.BLOCKING


def test_a_minimal_description_is_incomplete_and_says_what_is_essential() -> None:
    result = completeness("A food delivery platform.")
    assert result.status is CompletenessStatus.INCOMPLETE
    assert [p.name for p in result.profiles] == ["public_platform"]
    blocking = {f.code for f in result.findings if f.severity is Severity.BLOCKING}
    assert blocking == {"missing_traffic", "missing_latency", "missing_availability"}
    for finding in result.findings:
        assert finding.suggestion


def test_a_partial_description() -> None:
    result = completeness("A food delivery platform with 2K RPS at peak and p95 latency under 300 ms.")
    assert result.status is CompletenessStatus.INCOMPLETE
    assert {f.code for f in result.findings if f.severity is Severity.BLOCKING} == {"missing_availability"}
    assert set(result.covered) == {Area.TRAFFIC, Area.LATENCY}


def test_a_well_specified_description_is_complete() -> None:
    result = completeness(scenarios.FOOD_DELIVERY)
    assert result.status is CompletenessStatus.COMPLETE
    assert [f for f in result.findings if f.severity is not Severity.INFO] == []


# --- context ---------------------------------------------------------------------------------------


def test_an_internal_tool_is_not_asked_for_traffic_figures() -> None:
    result = completeness(scenarios.INTERNAL_ADMIN)
    assert result.status is CompletenessStatus.COMPLETE
    assert result.importance[Area.TRAFFIC] is Importance.OPTIONAL
    assert missing(scenarios.INTERNAL_ADMIN)["traffic"] == "info"
    assert [f for f in result.findings if f.severity is Severity.BLOCKING] == []


def test_an_internal_tool_without_access_control_is_incomplete() -> None:
    assert missing("An internal admin tool for our staff.")["security"] == "blocking"


def test_a_payment_system_needs_security_compliance_consistency_and_recovery() -> None:
    result = completeness("A payment service handling 500 requests per second.")
    blocking = {f.code for f in result.findings if f.severity is Severity.BLOCKING}
    assert blocking == {
        "missing_security",
        "missing_compliance",
        "missing_availability",
        "missing_reliability",
        "missing_data",
    }
    assert "payments must be secure" in next(
        f.message for f in result.findings if f.code == "missing_security"
    )


def test_the_strongest_rating_wins_across_profiles() -> None:
    # An internal payments tool: the internal profile relaxes traffic, payments makes security essential.
    result = completeness("An internal back office for payments.")
    names = {p.name for p in result.profiles}
    assert names == {"payments", "internal_tool"}
    assert result.importance[Area.SECURITY] is Importance.ESSENTIAL
    assert result.importance[Area.COMPLIANCE] is Importance.ESSENTIAL
    assert result.importance[Area.TRAFFIC] is Importance.OPTIONAL


def test_profiles_explain_themselves() -> None:
    [profile] = completeness("An IoT telemetry service for 2M devices.").profiles
    assert (profile.name, profile.evidence) == ("data_ingestion", ("iot", "telemetry", "devices"))


# --- the scenarios ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "status", "blocking"),
    [
        ("food_delivery", "complete", set()),
        ("ecommerce", "incomplete", {"missing_reliability"}),  # it takes payments: recovery is essential
        ("saas_dashboard", "complete", set()),
        ("fintech", "complete", set()),
        ("social", "complete", set()),
        ("iot_telemetry", "incomplete", {"missing_availability"}),
        ("internal_admin", "complete", set()),
    ],
)
def test_scenarios(name: str, status: str, blocking: set[str]) -> None:
    result = completeness(scenarios.ALL[name])
    assert result.status.value == status
    assert {f.code for f in result.findings if f.severity is Severity.BLOCKING} == blocking


# --- existing requirements count -------------------------------------------------------------------


def test_existing_requirements_cover_areas() -> None:
    text = "A food delivery platform with 2K RPS and p95 latency under 300 ms."
    existing = NewRequirement.create(
        project_id=uuid.uuid7(),
        created_by_user_id=uuid.uuid7(),
        type=RequirementType.AVAILABILITY,
        category="availability",
        title="Availability",
        statement="99.9 %",
        priority=RequirementPriority.HIGH,
        status=RequirementStatus.ACTIVE,
        structured_data={"metric": "availability", "operator": ">=", "value": "99.9", "unit": "%"},
    ).content
    validated = validate(extract(text))
    result = assess(text, [*(c.content for c in validated.candidates), existing])
    assert result.status is CompletenessStatus.COMPLETE


def test_every_profile_rates_only_known_areas_and_has_a_reason() -> None:
    for profile in PROFILES:
        assert profile.reason
        assert set(profile.ratings) <= set(Area)


def test_completeness_is_deterministic() -> None:
    assert completeness(scenarios.FINTECH) == completeness(scenarios.FINTECH)
