"""Assumption detection (Requirements Engine phase 6)."""

from decimal import Decimal

import pytest

from core.domain.requirements.analysis import Severity
from engines.requirements.ambiguity import find_ambiguities
from engines.requirements.assumptions import KINDS, find_assumptions
from engines.requirements.classifier import USER_COUNT_READINGS
from engines.requirements.extractor import extract
from engines.requirements.findings import Finding, FindingKind
from engines.requirements.validation import validate


def assumptions(text: str) -> list[Finding]:
    extraction = extract(text)
    return find_assumptions(extraction, validate(extraction))


def codes(text: str) -> list[str]:
    return [f.code for f in assumptions(text)]


# --- the spec's case: surfaced, not silently applied ------------------------------------------------


def test_ten_million_users_is_never_assumed_to_mean_anything() -> None:
    extraction = extract("We have 10M users.")
    validated = validate(extraction)
    # No reading was applied: no candidate, no assumption...
    assert validated.candidates == ()
    assert find_assumptions(extraction, validated) == []
    # ...the choice is surfaced instead, with every reading offered.
    [ambiguity] = find_ambiguities(validated)
    assert (ambiguity.code, ambiguity.options) == ("user_count_kind_unspecified", USER_COUNT_READINGS)


def test_a_qualified_user_count_needs_no_assumption_beyond_its_bound() -> None:
    assert codes("10M monthly active users.") == ["operator_implied"]
    assert codes("At least 10M monthly active users.") == []


# --- applied assumptions are all surfaced ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Support 2000 rps.", ["operator_implied"]),
        ("Support at least 2000 rps.", []),
        ("Retain data for 7 years.", ["year_as_365_days", "operator_implied"]),
        ("Retain data for at least 7 years.", ["year_as_365_days"]),
        ("Keep backups for at least 6 months.", ["month_as_30_days"]),
        ("Spend at most $500 per month.", ["dollar_as_usd"]),
        ("Spend at most €500 per month.", []),
        ("Storage between 10 and 20 GB.", ["unit_shared_in_range"]),
        ("Storage between 10 GB and 20 GB.", []),
    ],
)
def test_every_applied_interpretation_is_surfaced(text: str, expected: list[str]) -> None:
    assert codes(text) == expected


def test_an_assumption_carries_its_reason_confidence_candidate_and_source() -> None:
    extraction = extract("Spend at most $500 per month.")
    validated = validate(extraction)
    [finding] = find_assumptions(extraction, validated)
    [candidate] = validated.candidates
    assert finding.kind is FindingKind.ASSUMPTION
    assert finding.message == "“Monthly budget”: “$” was read as US dollars (USD)."
    assert finding.confidence == Decimal("0.8")
    assert finding.severity is Severity.WARNING  # the currency matters
    assert finding.options[0] == "USD"
    assert finding.candidate_keys == (candidate.key,)
    assert finding.span == candidate.span
    assert candidate.source.value == "system"  # which method made it
    assert finding.suggestion


def test_harmless_assumptions_are_info() -> None:
    for finding in assumptions("Retain data for 7 years."):
        assert finding.severity is Severity.INFO
        assert finding.confidence is not None
        assert finding.confidence >= Decimal("0.9")


def test_assumptions_of_dropped_candidates_are_not_reported() -> None:
    # 150 % availability is invalid (blocking); its implied operator no longer matters.
    assert codes("We need 150% availability.") == []
    # The second statement is a duplicate; its assumption is reported once, for the kept one.
    assert codes("Support 2000 rps. We need 2k requests per second.") == ["operator_implied"]


def test_every_known_assumption_has_guidance() -> None:
    for code, kind in KINDS.items():
        assert kind.suggestion, code
        assert Decimal(0) < kind.confidence <= Decimal(1), code


def test_assumption_detection_is_deterministic() -> None:
    text = "Support 2000 rps, retain logs for 2 years and spend at most $900 per month."
    assert assumptions(text) == assumptions(text)
    assert len({f.key for f in assumptions(text)}) == len(assumptions(text))
