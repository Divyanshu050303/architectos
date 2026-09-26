"""The Requirements Engine end to end: result shape, readiness, determinism (phase 10)."""

import json
import time
import uuid
from collections.abc import Callable

import pytest

from core.domain.requirements.analyses import MAX_INPUT_CHARACTERS
from core.domain.requirements.candidates import RequirementCandidate
from core.domain.requirements.entities import NewRequirement
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from engines.requirements.conflicts import Existing
from engines.requirements.service import (
    _GROUPS,
    ENGINE_VERSION,
    MAX_CANDIDATES,
    MAX_FINDINGS,
    RESULT_SCHEMA,
    RequirementsEngine,
    analyze,
)

from . import scenarios


def test_the_result_shape() -> None:
    result = analyze(scenarios.FOOD_DELIVERY)
    assert set(result) == {
        "result_schema",
        "engine_version",
        "input_sha256",
        "semantic",
        "candidates",
        "issues",
        "ambiguities",
        "assumptions",
        "conflicts",
        "completeness",
        "completeness_findings",
        "confidence",
        "ready_for_architecture",
        "blocking",
        "counts",
    }
    assert (result["result_schema"], result["engine_version"]) == (RESULT_SCHEMA, ENGINE_VERSION)
    json.dumps(result)  # storable as is


def test_a_well_specified_system_is_ready_for_architecture() -> None:
    result = analyze(scenarios.FOOD_DELIVERY)
    assert result["ready_for_architecture"] is True
    assert result["blocking"] == []
    assert result["completeness"]["status"] == "complete"
    assert result["counts"]["candidates"] == len(result["candidates"]) > 10
    assert result["confidence"]["lowest"] is not None


@pytest.mark.parametrize(
    ("text", "blocking_codes"),
    [
        ("The system should handle a lot of traffic.", {"nothing_to_assess"}),  # no system, no requirement
        ("A food delivery platform. Support at least 10,000 rps. Support at most 5,000 rps. p95 latency "
         "under 300 ms. 99.9% availability.", {"disjoint_bounds"}),
        ("A food delivery platform with 150% availability, 2000 rps and p95 latency under 200 ms.",
         {"out_of_range", "missing_availability"}),
        ("", {"nothing_to_assess"}),
    ],
)  # fmt: skip
def test_blocking_findings_decide_readiness(text: str, blocking_codes: set[str]) -> None:
    result = analyze(text)
    assert result["ready_for_architecture"] is False
    everything = [
        f
        for group in ("issues", "ambiguities", "assumptions", "conflicts", "completeness_findings")
        for f in result[group]
    ]
    blocking = [f for f in everything if f["severity"] == "blocking"]
    assert {f["code"] for f in blocking} == blocking_codes
    assert set(result["blocking"]) == {f["key"] for f in blocking}


def test_warnings_do_not_block() -> None:
    result = analyze(scenarios.SOCIAL)  # "Global users": a vague geography warning
    assert any(f["code"] == "vague_geography" for f in result["ambiguities"])
    assert result["ready_for_architecture"] is True


def test_findings_are_grouped_by_kind() -> None:
    result = analyze("A food delivery platform, fast, with 10M users. Support 2000 rps.")
    assert {f["kind"] for f in result["ambiguities"]} == {"ambiguity"}
    assert {f["kind"] for f in result["assumptions"]} == {"assumption"}
    assert {f["kind"] for f in result["completeness_findings"]} == {"completeness"}


def test_candidates_round_trip_through_the_stored_result() -> None:
    for text in scenarios.ALL.values():
        for stored in analyze(text)["candidates"]:
            candidate = RequirementCandidate.from_dict(json.loads(json.dumps(stored)))
            assert candidate.key == stored["key"]
            assert candidate.to_dict() | {"normalized_data": stored["normalized_data"]} == stored


def test_a_tampered_candidate_is_refused() -> None:
    stored = analyze("Support at least 2000 rps.")["candidates"][0]
    with pytest.raises(ValueError, match="key"):
        RequirementCandidate.from_dict(stored | {"category": "requests_per_second"})


def test_the_analysis_is_deterministic() -> None:
    for text in scenarios.ALL.values():
        assert analyze(text) == analyze(text)


def test_existing_requirements_take_part() -> None:
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
    text = (
        "A food delivery platform. Support 2000 rps, p95 latency under 300 ms, availability of exactly 99%."
    )
    alone, together = analyze(text), analyze(text, [Existing("REQ-1", 1, existing)])
    assert not [f for f in alone["conflicts"] if f["kind"] == "conflict"]
    [conflict] = [f for f in together["conflicts"] if f["kind"] == "conflict"]
    assert conflict["requirement_references"] == ["REQ-1@v1"]
    assert together["ready_for_architecture"] is False


async def test_the_engine_implements_the_domain_port() -> None:
    output = await RequirementsEngine().analyze(scenarios.FOOD_DELIVERY, [])
    assert output.engine_version == ENGINE_VERSION
    assert output.ready_for_architecture is True
    assert output.candidate_count == len(output.result["candidates"])
    assert output.blocking_count == 0


# --- worst cases -----------------------------------------------------------------------------------


def _fill(sentence: Callable[[int], str]) -> str:
    """As many generated sentences as fit in the largest accepted input."""
    parts, size, i = [], 0, 0
    while size + len(sentence(i)) <= MAX_INPUT_CHARACTERS:
        parts.append(sentence(i))
        size += len(parts[-1])
        i += 1
    return "".join(parts)


WORST_CASES = {
    # Every pair of distinct throughput targets is a consistency finding: quadratic without caps.
    "distinct_targets": _fill(lambda i: f"Support at least {i + 1} rps. "),
    "contradictions": _fill(lambda i: f"At least {2 * i + 2} rps. At most {i + 1} rps. "),
    "backwards_ranges": _fill(lambda i: f"Latency between {i + 9} and {i + 1} ms. "),
    "repeated_vague_words": _fill(lambda i: "fast "),
}


@pytest.mark.parametrize("name", WORST_CASES)
def test_the_worst_case_is_bounded_in_time_and_size(name: str) -> None:
    text = WORST_CASES[name]
    started = time.perf_counter()
    result = analyze(text)
    elapsed = time.perf_counter() - started
    size = len(json.dumps(result))
    findings = [f for group in _GROUPS for f in result[group]]
    assert elapsed < 3, f"{name}: {elapsed:.2f} s"  # about 0.2 s on a laptop
    assert size < 1024 * 1024, f"{name}: {size} bytes"  # stored results are capped at 4 MiB
    assert len(result["candidates"]) <= MAX_CANDIDATES
    assert len(findings) <= MAX_FINDINGS
    json.dumps(result)  # still JSON-ready


def test_what_is_left_out_is_said_and_blocking_findings_are_kept() -> None:
    result = analyze(WORST_CASES["contradictions"])
    codes = {f["code"] for f in result["issues"]}
    assert {"too_many_candidates", "findings_truncated"} <= codes
    assert result["ready_for_architecture"] is False
    assert result["counts"]["blocking"] == len(result["blocking"]) > 0


def test_a_repeated_vague_word_is_one_finding() -> None:
    result = analyze(WORST_CASES["repeated_vague_words"])
    [finding] = [f for f in result["ambiguities"] if f["code"] == "vague_latency"]
    assert finding["span"] == {"start": 0, "end": 4, "text": "fast"}
    assert "more times" in finding["message"]
