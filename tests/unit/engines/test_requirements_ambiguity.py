"""Ambiguity detection (Requirements Engine phase 5)."""

from decimal import Decimal

import pytest

from core.domain.requirements.analysis import Severity
from core.domain.requirements.candidates import ExtractionMethod, RequirementCandidate, SourceSpan
from engines.requirements.ambiguity import VAGUE, find_ambiguities
from engines.requirements.classifier import USER_COUNT_READINGS
from engines.requirements.extractor import Extraction, extract
from engines.requirements.findings import FindingKind
from engines.requirements.validation import validate

from .test_requirements_extraction import FOOD_DELIVERY


def codes(text: str) -> list[str]:
    return [f.code for f in find_ambiguities(validate(extract(text)))]


# --- the spec's cases ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("The system should handle high traffic.", "vague_traffic"),
        ("The API should be fast.", "vague_latency"),
        ("It has to work at large scale.", "vague_traffic"),
        ("We expect many users.", "vague_user_count"),
        ("The platform needs high availability.", "vague_availability"),
        ("It is a global system.", "vague_geography"),
        ("It should scale well.", "vague_scalability"),
        ("We never lose data.", "vague_reliability"),
        ("Keep it cheap.", "vague_cost"),
        ("We store lots of data.", "vague_data_volume"),
        ("Everything must be secure.", "vague_security"),
    ],
)
def test_vague_language(text: str, code: str) -> None:
    [finding] = find_ambiguities(validate(extract(text)))
    assert (finding.kind, finding.code, finding.severity) == (FindingKind.AMBIGUITY, code, Severity.WARNING)
    assert finding.span is not None
    assert finding.span.matches(text)
    assert finding.suggestion


def test_no_value_is_ever_invented() -> None:
    validated = validate(extract("The system should handle a lot of traffic and be fast."))
    assert [c for c in validated.candidates if c.content.constraint is not None] == []
    assert codes("The system should handle a lot of traffic and be fast.") == [
        "vague_traffic",
        "vague_latency",
    ]


def test_ten_million_users_is_ambiguous_with_every_reading_offered() -> None:
    [finding] = find_ambiguities(validate(extract("We have 10M users.")))
    assert (finding.code, finding.options) == ("user_count_kind_unspecified", USER_COUNT_READINGS)
    assert finding.span is not None
    assert finding.span.text == "10M users"


@pytest.mark.parametrize(
    ("text", "code", "options"),
    [
        (
            "Something within 5 minutes.",
            "duration_purpose_unspecified",
            ("latency", "rpo", "rto", "retention"),
        ),
        ("Around 40% is mobile.", "percentage_purpose_unspecified", ("availability", "durability")),
        ("Handle 2000 connections.", "unitless_number", ()),
    ],
)
def test_quantities_without_a_purpose(text: str, code: str, options: tuple[str, ...]) -> None:
    [finding] = find_ambiguities(validate(extract(text)))
    assert (finding.code, finding.options) == (code, options)


# --- resolved elsewhere ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The API should be fast: p95 under 300 ms.",
        "High traffic: at least 2000 rps at peak.",
        "Highly available, 99.95% uptime.",
        "Global, in eu-west-1 and us-east-1.",
        "Reliable: RPO under 5 minutes.",
        "Cheap: at most 500 EUR/month.",
        "Secure: encrypt data at rest.",
    ],
)
def test_a_vague_phrase_is_resolved_by_a_precise_statement(text: str) -> None:
    assert not [c for c in codes(text) if c.startswith("vague_")]


def test_the_well_specified_example_has_no_vague_language() -> None:
    assert [c for c in codes(FOOD_DELIVERY) if c.startswith("vague_")] == []


# --- incomplete candidates -------------------------------------------------------------------------


def test_latency_without_a_percentile() -> None:
    [finding] = find_ambiguities(validate(extract("Latency under 200 ms.")))
    assert (finding.code, finding.options, finding.field) == (
        "missing_percentile",
        ("p50", "p95", "p99"),
        "structured_data.percentile",
    )
    assert finding.candidate_keys
    assert codes("p95 latency under 200 ms.") == []


def test_encryption_should_say_where() -> None:
    assert codes("Encrypt customer data.") == ["encryption_scope_unspecified"]
    assert codes("Encrypt customer data at rest and in transit.") == []
    [finding] = find_ambiguities(validate(extract("Encrypt customer data.")))
    assert finding.severity is Severity.INFO


def test_low_confidence_interpretations_are_flagged() -> None:
    extracted = extract("Support 2000 rps.")
    [candidate] = extracted.candidates
    unsure = RequirementCandidate(ExtractionMethod.LLM, candidate.content, Decimal("0.55"), candidate.span)
    findings = find_ambiguities(validate(Extraction(extracted.raw_input, (unsure,), (), ())))
    assert [(f.code, f.candidate_keys) for f in findings] == [("low_confidence", (unsure.key,))]


def test_phrases_are_words_not_substrings() -> None:
    assert codes("We serve breakfast in a secured-parking area.") == []  # neither "fast" nor "secure"
    assert codes("Breakfast delivery.") == []  # "fast" inside a word is not vague language


def test_every_vague_phrase_has_a_suggestion_and_a_resolution() -> None:
    for vague in VAGUE:
        assert vague.suggestion
        assert vague.resolved_by


def test_ambiguity_detection_is_deterministic() -> None:
    text = "A fast, global platform for many users. 10M users. Encrypt data."
    first = find_ambiguities(validate(extract(text)))
    assert first == find_ambiguities(validate(extract(text)))
    assert [f.key for f in first] == [f.key for f in find_ambiguities(validate(extract(text)))]
    spans = [f.span for f in first if f.span]
    assert all(isinstance(s, SourceSpan) and s.matches(text) for s in spans)
