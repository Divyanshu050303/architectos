"""Validation of extracted candidates and the shared Finding model (Requirements Engine phase 4)."""

import random
from decimal import Decimal

import pytest

from core.domain.requirements.analysis import Severity
from core.domain.requirements.candidates import ExtractionMethod, RequirementCandidate, SourceSpan
from core.domain.requirements.entities import RequirementContent
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.value_objects import parse_structured_data
from engines.requirements.extractor import Extraction, extract
from engines.requirements.findings import Finding, FindingKind, ordered
from engines.requirements.validation import validate

from .test_requirements_extraction import FOOD_DELIVERY


def findings_of(text: str) -> list[tuple[str, str, str]]:
    return [(f.kind.value, f.code, f.severity.value) for f in validate(extract(text)).findings]


def test_a_clean_input_passes_untouched() -> None:
    extraction = extract(FOOD_DELIVERY)
    validated = validate(extraction)
    assert validated.candidates == extraction.candidates
    assert (validated.findings, validated.unresolved) == ((), ())


# --- the domain rules, applied to what people wrote ------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("We need 150% availability.", "out_of_range"),
        ("Latency must be 0 ms at p99.", "out_of_range"),
        ("Availability of more than 100%.", "unsatisfiable"),
        ("RPO less than 0 minutes.", "unsatisfiable"),
    ],
)
def test_impossible_requirements_are_blocking(text: str, code: str) -> None:
    validated = validate(extract(text))
    assert validated.candidates == ()
    [finding] = validated.findings
    assert (finding.kind, finding.code, finding.severity) == (FindingKind.INVALID, code, Severity.BLOCKING)
    assert finding.span is not None
    assert text[finding.span.start : finding.span.end] == finding.span.text
    assert finding.suggestion
    assert finding.candidate_keys


def test_an_empty_range_is_blocking() -> None:
    assert findings_of("Storage between 20GB and 10GB.") == [
        ("unresolved", "invalid_not_above_min", "blocking")
    ]


@pytest.mark.parametrize(
    ("text", "code", "severity"),
    [
        ("Support -50 rps.", "negative_value", "blocking"),
        ("Serve 1000000000000000 rps.", "out_of_range", "blocking"),
        ("Keep 5m records.", "ambiguous_unit", "warning"),
        ("Store 10 GiB.", "unsupported_unit", "warning"),
        ("Spend at most $500.", "unsupported_unit", "warning"),
    ],
)
def test_normalization_problems_become_findings(text: str, code: str, severity: str) -> None:
    assert findings_of(text) == [("unresolved", code, severity)]


def test_open_classifications_are_left_to_the_ambiguity_engines() -> None:
    validated = validate(extract("We have 10M users."))
    assert validated.findings == ()
    [item] = validated.unresolved
    assert item.reason == "user_count_kind_unspecified"


# --- duplicates ------------------------------------------------------------------------------------


def test_the_same_requirement_stated_twice_is_kept_once() -> None:
    validated = validate(extract("Support 2000 rps. We need 2k requests per second."))
    assert len(validated.candidates) == 1
    [finding] = validated.findings
    assert (finding.kind, finding.severity) == (FindingKind.DUPLICATE, Severity.INFO)
    assert finding.candidate_keys[0] == validated.candidates[0].key


def test_different_bounds_are_not_duplicates() -> None:
    validated = validate(extract("Support 2000 rps. Support at most 5000 rps."))
    assert len(validated.candidates) == 2


def test_the_same_bound_on_different_scopes_is_not_a_duplicate() -> None:
    validated = validate(extract("API latency under 100 ms. Database latency under 100 ms."))
    assert len(validated.candidates) == 2


# --- provenance ------------------------------------------------------------------------------------


def _candidate(span: SourceSpan | None) -> RequirementCandidate:
    content = RequirementContent(
        type=RequirementType.CAPACITY,
        category="throughput",
        title="Throughput",
        statement="Support 2000 rps.",
        priority=RequirementPriority.MEDIUM,
        status=RequirementStatus.DRAFT,
        constraint=parse_structured_data(
            {"metric": "requests_per_second", "operator": ">=", "value": "2000", "unit": "requests/second"}
        ),
    )
    return RequirementCandidate(ExtractionMethod.LLM, content, Decimal("0.9"), span)


def test_a_proposal_that_points_at_text_not_in_the_input_is_rejected() -> None:
    raw = "Support 2000 rps."
    invented = _candidate(SourceSpan(0, 8, "Handle 5"))
    honest = _candidate(SourceSpan(8, 16, "2000 rps"))
    validated = validate(Extraction(raw, (invented, honest), (), ()))
    assert validated.candidates == (honest,)
    [finding] = validated.findings
    assert (finding.kind, finding.code, finding.severity) == (
        FindingKind.REJECTED,
        "span_not_in_input",
        Severity.WARNING,
    )


def test_a_proposal_without_a_span_is_kept_but_carries_no_span() -> None:
    candidate = _candidate(None)
    assert validate(Extraction("Support 2000 rps.", (candidate,), (), ())).candidates == (candidate,)


# --- findings --------------------------------------------------------------------------------------


def test_finding_keys_are_deterministic_and_distinct() -> None:
    first = validate(extract("We need 150% availability. Support -5 rps.")).findings
    second = validate(extract("We need 150% availability. Support -5 rps.")).findings
    assert [f.key for f in first] == [f.key for f in second]
    assert len({f.key for f in first}) == 2
    assert all(f.key.startswith("find_") for f in first)


def test_findings_are_ordered_blocking_first_then_by_position() -> None:
    span = SourceSpan(5, 6, "x")
    info = Finding(FindingKind.DUPLICATE, "a", Severity.INFO, "m", span=SourceSpan(0, 1, "x"))
    late = Finding(FindingKind.INVALID, "b", Severity.BLOCKING, "m", span=SourceSpan(9, 10, "x"))
    early = Finding(FindingKind.INVALID, "c", Severity.BLOCKING, "m", span=span)
    warning = Finding(FindingKind.AMBIGUITY, "d", Severity.WARNING, "m")
    assert ordered([info, late, warning, early, info]) == (early, late, warning, info)
    assert early.blocking
    assert not warning.blocking


# --- robustness ------------------------------------------------------------------------------------


_FRAGMENTS = [
    "at least", "under", "between", "and", "to", "-", "2k", "1.5M", "300", "ms", "rps", "%", "GB", "gb",
    "users", "daily", "p95", "percentile", "latency", "availability", "RPO", "$", "€", "per month",
    "years", ",", ".", ";", "\n", "eu-west-1", "\u2212", "0", "99.999999999", "10", "between 20 and 10 GB",
    "encrypt", "PII", "exactly", "or more", "four nines", "API", "database", "  ",
]  # fmt: skip


def test_extraction_and_validation_never_raise_and_spans_always_match() -> None:
    rng = random.Random(20260926)  # noqa: S311 - deterministic fuzzing, not cryptography
    for _ in range(2000):
        text = " ".join(rng.choice(_FRAGMENTS) for _ in range(rng.randint(1, 14)))
        validated = validate(extract(text))
        for candidate in validated.candidates:
            assert candidate.problem() is None, (text, candidate)
            assert candidate.span is None or candidate.span.matches(text), text
        for finding in validated.findings:
            assert finding.span is None or finding.span.matches(text), text
