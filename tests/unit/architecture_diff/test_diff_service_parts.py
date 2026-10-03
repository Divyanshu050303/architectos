"""The parts the diff service composes (spec 15, 16): a stored diff and explanation run read back as
they were, or refused; the deterministic comparison of two resolved states; and the interpretation —
not needed for identical states, a retrieval failure said, an oversized diff never sent, no model
configured never invented, cited passages kept as citations."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from core.architecture_ir.serialization import content_hash
from core.domain.architecture_agent.requests import AgentUsage
from core.domain.architecture_agent.runs import RawOutput
from core.domain.architecture_diff.changes import SemanticDiff
from core.domain.architecture_diff.diffs import ArchitectureDiff
from core.domain.architecture_diff.errors import InvalidDiffRecord
from core.domain.architecture_diff.explanations import ExplanationRun
from core.domain.architecture_diff.ports import DiffInputs, ExplanationBudget, ImpactInputs, ResolvedState
from core.domain.architecture_diff.records import (
    diff_document,
    diff_from,
    explanation_document,
    explanation_run_from,
)
from core.domain.architecture_diff.references import ComparedState, StateRef
from core.domain.architecture_diff.values import ExplanationFailure, ExplanationStatus, ImpactStatus
from core.domain.knowledge.retrieval import RetrievalQuery, RetrievalResult
from engines.architecture_diff.engine import DeterministicDiffEngine
from engines.architecture_diff.interpreter import NOT_CONFIGURED, DiffInterpretation, build_interpreter
from tests.integration.api.diff_support import ContextLlm, scripted_interpreter
from tests.unit.architecture_agent.test_agent_context import passage
from tests.unit.architecture_diff.test_diff_domain import (
    BASE_HASH,
    a_diff,
    added,
    modified,
    request,
    semantic,
)
from tests.unit.architecture_diff.test_diff_impact import ENGINES, shop

NOW = datetime(2026, 10, 3, tzinfo=UTC)
USER = uuid.uuid4()
ARCH = uuid.uuid4()


def resolved(ir: Any, number: int) -> ResolvedState:
    ref = StateRef.revision(ARCH, number)
    return ResolvedState(ComparedState(ref, content_hash(ir), f"Shop r{number}"), ir, ARCH, number)


def identical() -> ArchitectureDiff:
    asked = request()
    return a_diff(
        target=ComparedState(asked.target, BASE_HASH, "Orders v2"),
        semantic=SemanticDiff(BASE_HASH, BASE_HASH),
    )


# --- records ---------------------------------------------------------------------------------------


def test_a_stored_diff_reads_back_as_it_was() -> None:
    diff = a_diff()
    assert diff_from(diff_document(diff)) == diff


def test_a_stored_diff_that_does_not_hold_is_refused() -> None:
    row = diff_document(a_diff())
    row["semantic"] = row["semantic"] | {"groups": []}  # a change in no group
    with pytest.raises(InvalidDiffRecord):
        diff_from(row)
    with pytest.raises(InvalidDiffRecord):
        diff_from(diff_document(a_diff()) | {"base_kind": "latest"})


def test_an_explanation_run_reads_back_as_it_was() -> None:
    run = ExplanationRun(
        uuid.uuid4(), uuid.uuid4(), ExplanationStatus.FAILED, USER, NOW, "scripted/test-model",
        "diff-explanation-v1", AgentUsage(1, 900, 200, 5), RawOutput("a" * 64, 120),
        failure=ExplanationFailure.EXPLANATION_REJECTED, limitations=("Said.",),
    )  # fmt: skip
    document = explanation_document(uuid.uuid4(), run)
    assert explanation_run_from(document) == run
    with pytest.raises(InvalidDiffRecord):
        explanation_run_from(document | {"failure": None})  # failed, with no reason


# --- the deterministic comparison -------------------------------------------------------------------


def test_two_resolved_states_compare_with_every_engine() -> None:
    computer = DeterministicDiffEngine(ENGINES)
    base, target = resolved(shop(), 1), resolved(shop(db={"exposure": "public"}), 2)
    outcome = computer.compare(base, target, DiffInputs(ImpactInputs(), {}))
    [change] = outcome.semantic.changes
    assert change.element_id == "db"
    statuses = {e.engine: e.status for e in outcome.engines}
    assert statuses["security"] is ImpactStatus.EVALUATED
    assert statuses["capacity"] is statuses["cost"] is ImpactStatus.NOT_EVALUATED
    assert computer.compare(base, target, DiffInputs(ImpactInputs(), {})) == outcome


# --- the interpretation --------------------------------------------------------------------------------


async def nothing(query: RetrievalQuery) -> RetrievalResult:
    return RetrievalResult(passages=(), searched_sources=0)


async def interpret(
    interpreter: DiffInterpretation,
    diff: ArchitectureDiff | None = None,
    retrieve: Any = nothing,
    budget: ExplanationBudget | None = None,
) -> ExplanationRun:
    return await interpreter.interpret(
        diff or a_diff(), retrieve, budget or ExplanationBudget(), run_id=uuid.uuid4(), user_id=USER, now=NOW
    )


async def test_identical_states_are_not_explained() -> None:
    llm = ContextLlm()
    run = await interpret(scripted_interpreter(llm), identical())
    assert run.status is ExplanationStatus.NOT_NEEDED
    assert llm.requests == []
    assert run.usage.retrieval_calls == 0


async def test_an_explanation_keeps_only_the_passages_it_cites() -> None:
    async def found(query: RetrievalQuery) -> RetrievalResult:
        return RetrievalResult(passages=(passage("kch_a", 1), passage("kch_b", 2)), searched_sources=1)

    cited = {"text": "The runbook covers this.", "groundings": [{"basis": "evidence", "ref": "kch_a"}]}
    llm = ContextLlm(tradeoffs=[cited | {"inferred": False}])
    run = await interpret(scripted_interpreter(llm), retrieve=found)
    assert run.status is ExplanationStatus.COMPLETED, run.rejections
    assert [e.chunk_id for e in run.evidence] == ["kch_a"]  # given two, cited one
    assert run.usage.retrieval_calls == 1
    assert run.usage.model_calls == 1


async def test_a_retrieval_failure_is_said_and_the_explanation_proceeds() -> None:
    async def broken(query: RetrievalQuery) -> RetrievalResult:
        raise PermissionError

    run = await interpret(scripted_interpreter(), retrieve=broken)
    assert run.status is ExplanationStatus.COMPLETED
    assert any("could not be retrieved (PermissionError)" in limit for limit in run.limitations)


async def test_a_diff_too_large_for_the_budget_is_never_sent() -> None:
    llm = ContextLlm()
    big = a_diff(semantic=semantic(*(added(f"cache-{i:03}") for i in range(40)), modified()))
    run = await interpret(scripted_interpreter(llm), big, budget=ExplanationBudget(max_context_chars=1000))
    assert run.status is ExplanationStatus.FAILED
    assert run.failure is ExplanationFailure.BUDGET_EXHAUSTED
    assert llm.requests == []


async def test_without_a_model_nothing_is_invented() -> None:
    interpreter = build_interpreter(provider="none", api_key=None, model="unused", timeout_seconds=10)
    run = await interpret(interpreter)
    assert run.status is ExplanationStatus.FAILED
    assert run.failure is ExplanationFailure.LLM_UNAVAILABLE
    assert run.model == NOT_CONFIGURED
    assert run.explanation is None
