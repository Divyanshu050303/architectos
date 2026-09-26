"""Classification and extraction (Requirements Engine phase 3)."""

import uuid
from decimal import Decimal

import pytest

from core.domain.requirements.candidates import ExtractionMethod, RequirementCandidate
from core.domain.requirements.enums import RequirementPriority, RequirementScope, RequirementSource
from engines.requirements.classifier import USER_COUNT_READINGS, priority_of
from engines.requirements.extractor import MAX_STATEMENT, extract, sentences, statement_around

FOOD_DELIVERY = (
    "I want to build a food delivery platform. It should support 100,000 daily users, around 2,000 "
    "requests per second, and the API should have p95 latency below 300ms.\n"
    "- 500 orders/sec at peak\n"
    "- 99.9% availability\n"
    "- RPO under 5 minutes; restore within 1 hour\n"
    "- Encrypt PII and retain data for 7 years.\n"
    "Payments must be PCI compliant. Deploy to eu-west-1 and eu-central-1. Use PostgreSQL."
)


def only(text: str) -> RequirementCandidate:
    extraction = extract(text)
    assert extraction.unresolved == (), extraction.unresolved
    [candidate] = extraction.candidates
    return candidate


def summary(candidate: RequirementCandidate) -> tuple[str, str, dict[str, object]]:
    content = candidate.content
    return content.type.value, content.category, content.structured_data


# --- the spec's classification cases ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "type_", "category", "data"),
    [
        (
            "The API must handle 2000 requests/sec.",
            "capacity",
            "throughput",
            {"metric": "requests_per_second", "operator": ">=", "value": "2000", "unit": "requests/second"},
        ),
        (
            "p95 latency under 300ms",
            "performance",
            "latency",
            {"metric": "latency", "operator": "<", "value": "300", "unit": "ms", "percentile": "95"},
        ),
        (
            "99.99% availability",
            "availability",
            "availability",
            {"metric": "availability", "operator": ">=", "value": "99.99", "unit": "%"},
        ),
        (
            "RPO under 5 minutes",
            "reliability",
            "rpo",
            {"metric": "rpo", "operator": "<", "value": "5", "unit": "min"},
        ),
        (
            "Retain data for 7 years.",
            "data",
            "retention",
            {"metric": "retention", "operator": ">=", "value": "2555", "unit": "d"},
        ),
        ("Encrypt PII.", "security", "encryption", {}),
    ],
)
def test_spec_classification_cases(text: str, type_: str, category: str, data: dict[str, object]) -> None:
    candidates = extract(text).candidates
    assert summary(candidates[0]) == (type_, category, data)


def test_encrypt_pii_yields_both_security_concerns() -> None:
    assert [c.content.category for c in extract("Encrypt PII.").candidates] == ["encryption", "pii"]


@pytest.mark.parametrize(
    ("text", "category", "metric"),
    [
        ("Recover within 30 minutes after a failure.", "rto", "rto"),
        ("Response times must stay within 2 seconds.", "latency", "latency"),
        ("Keep backups for 30 days.", "retention", "retention"),
        ("Aim for 99.95% uptime.", "uptime", "availability"),
        ("Eleven nines is too much; 99.999999% durability is enough.", "durability", "durability"),
        ("Plan for 2 TB of storage.", "storage", "storage"),
        ("We ingest 500 GB per day.", "data_volume", "storage"),
        ("Budget: $5,000 per month for cloud infrastructure.", "infrastructure_budget", "monthly_budget"),
        ("Keep costs under EUR 800/month.", "budget", "monthly_budget"),
        ("5000 concurrent users", "concurrent_users", "concurrent_users"),
        ("10M monthly active users", "monthly_active_users", "monthly_active_users"),
        ("30k orders per minute", "orders_per_second", "orders_per_second"),
    ],
)
def test_more_classifications(text: str, category: str, metric: str) -> None:
    candidate = extract(text).candidates[0]
    assert (candidate.content.category, candidate.content.structured_data["metric"]) == (category, metric)
    assert candidate.problem() is None


# --- context -----------------------------------------------------------------------------------------


def test_each_quantity_reads_its_own_clause() -> None:
    extraction = extract("p95 latency below 300ms and 99.9% availability")
    assert [c.content.category for c in extraction.candidates] == ["latency", "availability"]


def test_scope_only_where_the_clause_names_one() -> None:
    extraction = extract(
        "Support 2,000 requests per second, and the API should have p95 latency below 300ms. "
        "Database latency under 50 ms."
    )
    assert [(c.content.category, c.content.scope) for c in extraction.candidates] == [
        ("throughput", RequirementScope.SYSTEM),
        ("latency", RequirementScope.API),
        ("latency", RequirementScope.DATABASE),
    ]


def test_a_keyword_outside_the_clause_is_used_when_the_clause_has_none() -> None:
    candidate = only("Latency, measured at p99, should stay under 1 second.")
    assert candidate.content.structured_data == {
        "metric": "latency",
        "operator": "<",
        "value": "1",
        "unit": "s",
        "percentile": "99",
    }


# --- nothing invented ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reason", "options"),
    [
        ("We have 10M users.", "user_count_kind_unspecified", USER_COUNT_READINGS),
        ("It should respond quickly, within 5 minutes of an event.", None, None),
        ("Handle 2000 connections.", "unitless_number", ()),
        (
            "Something within 5 minutes.",
            "duration_purpose_unspecified",
            ("latency", "rpo", "rto", "retention"),
        ),
        (
            "Around 40% of traffic is mobile.",
            "percentage_purpose_unspecified",
            ("availability", "durability"),
        ),
        ("Keep 5m records.", "ambiguous_unit", ()),
    ],
)
def test_what_cannot_be_read_is_unresolved_not_guessed(
    text: str, reason: str | None, options: tuple[str, ...] | None
) -> None:
    extraction = extract(text)
    if reason is None:  # "respond ... within 5 minutes": a latency, read from "respond"
        assert extraction.candidates[0].content.category == "latency"
        return
    [unresolved] = extraction.unresolved
    assert (unresolved.reason, unresolved.options) == (reason, options)
    assert text[unresolved.span.start : unresolved.span.end] == unresolved.span.text
    assert [c for c in extraction.candidates if c.content.constraint is not None] == []


@pytest.mark.parametrize(
    "text", ["The system should handle a lot of traffic.", "The API should be fast.", "It must scale well."]
)
def test_vague_statements_produce_no_numbers(text: str) -> None:
    assert [c for c in extract(text).candidates if c.content.constraint is not None] == []


# --- qualitative -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Payments must be PCI compliant.", [("functional", "payment"), ("compliance", "pci_dss")]),
        (
            "Users log in with SSO and MFA.",
            [("functional", "authentication"), ("security", "authentication")],
        ),
        ("We are subject to GDPR and HIPAA.", [("compliance", "gdpr"), ("compliance", "hipaa")]),
        ("Orders need strong consistency.", [("functional", "order"), ("data", "consistency")]),
        (
            "Send push notifications and support search.",
            [("functional", "notification"), ("functional", "search")],
        ),
        ("Store API keys in a secrets manager.", [("security", "secrets")]),
        ("Use PostgreSQL and Kafka.", [("operational", "technology")]),
        ("Real-time tracking of couriers.", [("functional", "tracking")]),
    ],
)
def test_qualitative_requirements(text: str, expected: list[tuple[str, str]]) -> None:
    found = [(c.content.type.value, c.content.category) for c in extract(text).candidates]
    assert sorted(found) == sorted(expected)


def test_regions_become_a_set_constraint() -> None:
    candidate = only("Deploy to eu-west-1 and eu-central-1.")
    assert candidate.content.structured_data == {
        "metric": "regions",
        "operator": "in",
        "values": ["eu-central-1", "eu-west-1"],
    }
    assert candidate.problem() is None


def test_a_quantity_is_not_also_a_feature() -> None:
    assert [c.content.category for c in extract("500 orders/sec at peak").candidates] == ["orders_per_second"]


def test_a_qualitative_category_is_proposed_once_per_input() -> None:
    categories = [c.content.category for c in extract("Encrypt data. Encrypt backups too.").candidates]
    assert categories == ["encryption"]


# --- candidates ------------------------------------------------------------------------------------


def test_the_food_delivery_platform() -> None:
    extraction = extract(FOOD_DELIVERY)
    found = [
        (c.content.category, c.content.scope.value, c.content.structured_data) for c in extraction.candidates
    ]
    assert found == [
        (
            "daily_active_users",
            "system",
            {"metric": "daily_active_users", "operator": ">=", "value": "100000", "unit": "users"},
        ),
        (
            "throughput",
            "system",
            {"metric": "requests_per_second", "operator": ">=", "value": "2000", "unit": "requests/second"},
        ),
        (
            "latency",
            "api",
            {"metric": "latency", "operator": "<", "value": "300", "unit": "ms", "percentile": "95"},
        ),
        (
            "orders_per_second",
            "system",
            {"metric": "orders_per_second", "operator": ">=", "value": "500", "unit": "orders/second"},
        ),
        (
            "availability",
            "system",
            {"metric": "availability", "operator": ">=", "value": "99.9", "unit": "%"},
        ),
        ("rpo", "system", {"metric": "rpo", "operator": "<", "value": "5", "unit": "min"}),
        ("rto", "system", {"metric": "rto", "operator": "<=", "value": "1", "unit": "h"}),
        ("retention", "system", {"metric": "retention", "operator": ">=", "value": "2555", "unit": "d"}),
        ("encryption", "system", {}),
        ("pii", "system", {}),
        ("payment", "system", {}),
        ("pci_dss", "system", {}),
        (
            "regions",
            "system",
            {"metric": "regions", "operator": "in", "values": ["eu-central-1", "eu-west-1"]},
        ),
        ("technology", "system", {}),
    ]
    assert extraction.unresolved == ()


def test_candidates_are_system_proposals_that_would_validate() -> None:
    extraction = extract(FOOD_DELIVERY)
    for candidate in extraction.candidates:
        assert (candidate.method, candidate.source) == (ExtractionMethod.PATTERN, RequirementSource.SYSTEM)
        assert candidate.problem() is None, candidate
        assert candidate.span is not None
        assert candidate.span.matches(FOOD_DELIVERY)
        assert FOOD_DELIVERY[candidate.span.start : candidate.span.end] == candidate.span.text
        assert Decimal(0) < candidate.confidence <= Decimal(1)
    assert len({c.key for c in extraction.candidates}) == len(extraction.candidates)


def test_the_statement_is_the_users_own_sentence() -> None:
    candidate = extract(FOOD_DELIVERY).candidates[2]
    assert candidate.content.statement == (
        "It should support 100,000 daily users, around 2,000 requests per second, and the API should "
        "have p95 latency below 300ms."
    )
    assert candidate.content.statement in FOOD_DELIVERY


def test_implied_operators_and_conversions_are_noted() -> None:
    extraction = extract("Keep data for 7 years. Latency under 200 ms.")
    retention, latency = extraction.candidates
    codes = {(n.candidate_key, n.interpretation.code) for n in extraction.notes}
    assert codes == {(retention.key, "year_as_365_days"), (retention.key, "operator_implied")}
    assert latency.confidence > retention.confidence  # stated operator: more certain


def test_confidence_reflects_how_the_reading_was_decided() -> None:
    assert only("at least 2000 rps").confidence == Decimal("0.95")  # unit and operator stated
    assert only("2000 rps").confidence == Decimal("0.90")  # operator implied
    assert only("latency under 200 ms").confidence == Decimal("0.9")  # keyword decided
    assert only("Encrypt everything.").confidence == Decimal("0.8")  # qualitative


def test_extraction_is_deterministic() -> None:
    assert extract(FOOD_DELIVERY) == extract(FOOD_DELIVERY)


# --- sentences and priority ------------------------------------------------------------------------


def test_sentences_keep_their_offsets_and_drop_bullets() -> None:
    raw = "- first item\n2) second item. Third one; fourth\n\n* fifth"
    found = sentences(raw)
    assert [s.text for s in found] == ["first item", "second item.", "Third one;", "fourth", "fifth"]
    assert all(raw[s.start : s.end] == s.text for s in found)


def test_decimals_do_not_split_sentences() -> None:
    assert len(sentences("Aim for 99.9% availability. Then 99.99%.")) == 2


@pytest.mark.parametrize(
    ("sentence", "priority"),
    [
        ("This is mission-critical.", RequirementPriority.CRITICAL),
        ("Payments must be PCI compliant.", RequirementPriority.HIGH),
        ("Dark mode is nice to have.", RequirementPriority.LOW),
        ("Support 2000 rps.", RequirementPriority.MEDIUM),
    ],
)
def test_priority_is_proposed_only_from_explicit_words(sentence: str, priority: RequirementPriority) -> None:
    assert priority_of(sentence) is priority


def test_a_long_sentence_gives_each_requirement_its_own_clause() -> None:
    """A list with no full stop is one sentence; each statement is still the user's words for that
    requirement, short enough to promote."""
    text = "".join(f"at least {i + 1} rps, " for i in range(1500))[:20_000]
    extraction = extract(text)
    assert extraction.candidates
    for candidate in extraction.candidates:
        statement = candidate.content.statement
        assert statement in text
        assert len(statement) <= MAX_STATEMENT
        assert candidate.span is not None
        assert candidate.span.text in statement
        candidate.to_new_requirement(project_id=uuid.uuid4(), created_by_user_id=uuid.uuid4())  # valid
    assert extraction.candidates[5].content.statement == "at least 6 rps"


@pytest.mark.parametrize(
    ("text", "lo", "hi", "expected"),
    [
        ("Support at least 2000 rps.", 8, 25, "Support at least 2000 rps."),  # short: the sentence
        ("x" * 500, 10, 20, "x" * MAX_STATEMENT),  # no clause boundary: a window around the span
        ("a" * 450 + ", at least 2000 rps, " + "b" * 450, 452, 469, "at least 2000 rps"),
    ],
)
def test_statement_around(text: str, lo: int, hi: int, expected: str) -> None:
    statement = statement_around(text, lo, hi)
    assert statement == expected
    assert text[lo:hi] in statement
