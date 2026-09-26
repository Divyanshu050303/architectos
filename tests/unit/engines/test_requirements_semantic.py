"""Deterministic first; the model only where the rules need help (Requirements Engine phase 11)."""

from typing import Any

from ai.agents.requirement_agent import PROMPT_VERSION, RequirementExtractionAgent
from ai.llm.client import LlmTimeout
from engines.requirements.extractor import extract
from engines.requirements.semantic import needs_semantic
from engines.requirements.service import ENGINE_VERSION, RequirementsEngine

from ..ai.fakes import ScriptedLlm, proposal

COVERED = "A food delivery platform. Support at least 2000 rps, p95 latency under 300 ms, 99.9% availability."
UNCOVERED = COVERED + " The chat service keeps 2000 simultaneous sessions open at peak."


def engine(llm: ScriptedLlm | None) -> RequirementsEngine:
    return RequirementsEngine(RequirementExtractionAgent(llm) if llm else None)


async def result_of(text: str, llm: ScriptedLlm | None) -> dict[str, Any]:
    return (await engine(llm).analyze(text, [])).result


def test_when_the_rules_need_help() -> None:
    assert needs_semantic(extract(COVERED)) is None
    assert needs_semantic(extract(UNCOVERED)) == "unresolved_text"  # "2000 … sessions": no known unit
    assert needs_semantic(extract(COVERED + " Users should be able to export their data as CSV.")) == (
        "uncovered_sentences"
    )
    assert needs_semantic(extract("We have 10M users.")) == "unresolved_text"
    assert needs_semantic(extract("Nice weather today.")) is None  # nothing requirement-like


async def test_the_model_is_not_asked_when_the_rules_read_everything() -> None:
    llm = ScriptedLlm({"requirements": []})
    result = await result_of(COVERED, llm)
    assert llm.requests == []
    assert result["semantic"] == {"used": False, "reason": None}
    assert result["engine_version"] == ENGINE_VERSION


async def test_without_a_model_the_engine_says_so() -> None:
    result = await result_of(UNCOVERED, None)
    assert result["semantic"] == {"used": False, "reason": "not_configured"}


async def test_the_model_fills_what_the_rules_could_not_read() -> None:
    llm = ScriptedLlm({"requirements": [proposal()]})
    result = await result_of(UNCOVERED, llm)
    assert len(llm.requests) == 1
    ai = [c for c in result["candidates"] if c["source"] == "ai"]
    assert [c["span"]["text"] for c in ai] == ["2000 simultaneous sessions"]
    assert ai[0]["confidence"] == "0.8"
    assert result["semantic"] == {
        "used": True,
        "reason": "unresolved_text",
        "source": "scripted/test-model",
        "prompt_version": PROMPT_VERSION,
        "status": "ok",
        "accepted": 1,
        "rejected": 0,
        "usage": {"input_tokens": 120, "output_tokens": 40, "latency_ms": 5},
    }
    assert result["engine_version"] == f"{ENGINE_VERSION}+scripted/test-model#{PROMPT_VERSION}"


async def test_a_deterministic_reading_wins_over_the_models() -> None:
    same = proposal(
        type="capacity",
        category="throughput",
        title="Throughput",
        quote="at least 2000 rps",
        metric="requests_per_second",
        value="9999",
        unit="requests/second",
    )
    result = await result_of(UNCOVERED, ScriptedLlm({"requirements": [same, proposal()]}))
    throughput = [c for c in result["candidates"] if c["category"] == "throughput"]
    assert [(c["source"], c["structured_data"]["value"]) for c in throughput] == [("system", "2000")]


async def test_a_model_failure_leaves_the_deterministic_analysis_standing() -> None:
    result = await result_of(UNCOVERED, ScriptedLlm(error=LlmTimeout()))
    assert result["semantic"]["status"] == "llm_timeout"
    assert result["engine_version"] == ENGINE_VERSION
    [finding] = [f for f in result["issues"] if f["kind"] == "extraction"]
    assert (finding["code"], finding["severity"]) == ("llm_timeout", "warning")
    assert result["ready_for_architecture"] is True  # a warning, not a blocker
    assert {c["source"] for c in result["candidates"]} == {"system"}


async def test_rejected_proposals_are_reported_but_never_used() -> None:
    result = await result_of(
        UNCOVERED, ScriptedLlm({"requirements": [proposal(value="-5"), proposal(quote="made up")]})
    )
    assert {c["source"] for c in result["candidates"]} == {"system"}
    codes = sorted(f["code"] for f in result["issues"] if f["kind"] == "rejected")
    assert codes == ["llm_proposal_invalid", "llm_quote_not_in_input"]


async def test_an_ambiguity_stays_even_if_the_model_proposes_a_reading() -> None:
    """The model may suggest that "10M users" means monthly active users; a person still decides."""
    reading = proposal(
        category="monthly_active_users", quote="10M users", metric="monthly_active_users", value="10000000"
    )
    result = await result_of(
        "A social platform. We have 10M users.", ScriptedLlm({"requirements": [reading]})
    )
    assert any(f["code"] == "user_count_kind_unspecified" for f in result["ambiguities"])
    [ai] = [c for c in result["candidates"] if c["source"] == "ai"]
    assert ai["confidence"] == "0.8"  # a draft proposal, never authoritative
