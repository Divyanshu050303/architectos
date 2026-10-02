import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from ai.agents.architecture_agent import ArchitectureProposalAgent
from ai.llm.client import LlmTimeout, LlmUnavailable
from core.domain.architecture_agent.errors import InvalidAgentTransition
from core.domain.architecture_agent.proposals import Answer
from core.domain.architecture_agent.requests import AgentRequest, Budget
from core.domain.architecture_agent.runs import AgentRun
from core.domain.architecture_agent.values import (
    EngineStatus,
    FailureCode,
    QuestionKind,
    RunStatus,
    Stage,
)
from core.domain.knowledge.errors import InvalidKnowledgeRequest
from core.domain.knowledge.retrieval import RetrievalQuery, RetrievalResult
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import NewRequirement, Requirement
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.planning import build_planning_input
from engines.architecture_agent.orchestrator import AgentEngines, ArchitectureAgentPipeline, PassInputs
from engines.architecture_agent.reports import failed_report, not_evaluated_reports, validation_blocking
from engines.observability.service import DeterministicObservabilityEngine
from engines.reliability.service import DeterministicReliabilityEngine
from engines.security.service import DeterministicSecurityEngine
from engines.validation.service import DeterministicValidationEngine
from persistence.component_catalog import default_catalog
from tests.unit.ai.test_architecture_agent import Clock, SequencedLlm, output
from tests.unit.architecture_agent.test_agent_context import passage
from tests.unit.requirements.test_planning_input import PROJECT

CATALOG = default_catalog()
NOW = datetime(2026, 10, 3, tzinfo=UTC)
USER = uuid.uuid4()
ENGINES = AgentEngines(
    DeterministicValidationEngine(catalog=CATALOG),
    DeterministicReliabilityEngine(),
    DeterministicSecurityEngine(),
    DeterministicObservabilityEngine(),
)


def req(number: int, type_: RequirementType, category: str, data: dict[str, str]) -> Requirement:
    created = NewRequirement.create(
        project_id=PROJECT.id,
        created_by_user_id=uuid.uuid7(),
        type=type_,
        category=category,
        title=f"A {category} requirement",
        statement="Statement.",
        priority=RequirementPriority.HIGH,
        status=RequirementStatus.ACTIVE,
        structured_data=data,
    )
    return Requirement(
        id=uuid.UUID(int=number),
        project_id=PROJECT.id,
        number=number,
        version=1,
        content=created.content,
        source=created.source,
        confidence=created.confidence,
        created_by_user_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def bound(metric: str, operator: str, value: str, unit: str, **extra: str) -> dict[str, str]:
    return {"metric": metric, "operator": operator, "value": value, "unit": unit, **extra}


LATENCY = req(
    1, RequirementType.PERFORMANCE, "latency", bound("latency", "<=", "200", "ms", percentile="p95")
)
AVAILABILITY = req(2, RequirementType.AVAILABILITY, "availability", bound("availability", ">=", "99.9", "%"))
TRAFFIC = req(
    3, RequirementType.CAPACITY, "throughput", bound("requests_per_second", ">=", "500", "requests/second")
)
COVERED = (LATENCY, AVAILABILITY, TRAFFIC)


class Retriever:
    def __init__(self, *results: RetrievalResult, error: Exception | None = None) -> None:
        self.results = list(results)
        self.error = error
        self.queries: list[RetrievalQuery] = []

    async def __call__(self, query: RetrievalQuery) -> RetrievalResult:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.results.pop(0) if self.results else RetrievalResult((), 0)


def a_run(**overrides: Any) -> AgentRun:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": PROJECT.id,
        "requested_by_user_id": USER,
        "requested_at": NOW,
        "request": AgentRequest(uuid.uuid4(), "An order service for a web shop"),
    }
    return AgentRun(**(fields | overrides))


def inputs(requirements: tuple[Requirement, ...] = COVERED, retriever: Retriever | None = None) -> PassInputs:
    return PassInputs(
        build_planning_input(PROJECT, list(requirements)),
        requirements,
        ArchitecturePolicy(),
        retriever or Retriever(RetrievalResult((passage("kch_runbook", 1),), 1)),
    )


def pipeline(
    llm: SequencedLlm, *, step: float = 0.0, engines: AgentEngines = ENGINES
) -> ArchitectureAgentPipeline:
    proposer = ArchitectureProposalAgent(llm, clock=Clock(step))
    return ArchitectureAgentPipeline(proposer, engines, CATALOG, clock=lambda: NOW, monotonic=Clock(step))


async def test_a_covered_set_becomes_a_validated_candidate() -> None:
    run = await pipeline(SequencedLlm(output())).advance(a_run(), inputs())
    assert run.status is RunStatus.CANDIDATE_READY, (run.failure, run.rejections)
    assert run.stage is Stage.REVIEW
    assert run.candidate is not None
    assert run.proposal is not None
    assert {r.engine: r.status for r in run.reports} == {
        "validation": EngineStatus.EVALUATED,
        "reliability": EngineStatus.EVALUATED,
        "security": EngineStatus.EVALUATED,
        "observability": EngineStatus.EVALUATED,
        "capacity": EngineStatus.NOT_EVALUATED,
        "cost": EngineStatus.NOT_EVALUATED,
        "simulation": EngineStatus.NOT_EVALUATED,
    }
    assert run.usage.model_calls == 1
    assert run.usage.engine_runs == 4
    assert run.usage.retrieval_calls == 2
    assert run.model == "scripted/test-model"
    assert run.prompt_version == "architecture-proposal-v1"
    assert run.raw_output is not None
    assert [e.status for e in run.history] == [RunStatus.RUNNING, RunStatus.CANDIDATE_READY]


async def test_the_proposers_questions_are_kept_without_blocking() -> None:
    run = await pipeline(SequencedLlm(output())).advance(a_run(), inputs())
    proposer = [q for q in run.questions if q.kind is QuestionKind.PROPOSER]
    assert [q.question for q in proposer] == ["What is the peak order rate?"]
    assert not any(q.blocking for q in run.questions)


async def test_blocking_gaps_wait_for_a_person_then_resume() -> None:
    llm = SequencedLlm(output())
    agent = pipeline(llm)
    waiting = await agent.advance(a_run(), inputs((LATENCY,)))
    assert waiting.status is RunStatus.AWAITING_CLARIFICATION
    assert llm.requests == []  # the model is not called on an unanswered gap
    stated = "About 300 requests per second, 99.9%"
    resumed = waiting.answer(tuple(Answer(q.id, stated, USER, NOW) for q in waiting.unanswered), USER, NOW)
    ready = await agent.advance(resumed, inputs((LATENCY,)))
    assert ready.status is RunStatus.CANDIDATE_READY, (ready.failure, ready.rejections)
    assert ready.id == waiting.id
    [request] = llm.requests
    assert stated in request.user_content  # the person's words, as data


async def test_a_model_failure_fails_the_run_without_a_candidate() -> None:
    run = await pipeline(SequencedLlm(LlmUnavailable("AuthenticationError"))).advance(a_run(), inputs())
    assert run.status is RunStatus.FAILED
    assert run.failure is not None
    assert run.failure.code is FailureCode.LLM_UNAVAILABLE
    assert run.failure.stage is Stage.PROPOSAL
    assert run.candidate is None
    assert "Authentication" not in run.failure.message  # the provider's error is not shown


async def test_a_retry_is_counted_against_the_run() -> None:
    run = await pipeline(SequencedLlm(LlmTimeout(), output())).advance(a_run(), inputs())
    assert run.status is RunStatus.CANDIDATE_READY
    assert run.usage.model_calls == 2
    assert run.usage.input_tokens is None  # the timed-out call's tokens are unknown


async def test_a_rejected_proposal_keeps_the_proposal_and_why() -> None:
    node = output()["nodes"][0] | {"configuration": [{"property": "bogus", "value": 1}]}
    run = await pipeline(SequencedLlm(output(nodes=[node], connections=[]))).advance(a_run(), inputs())
    assert run.status is RunStatus.FAILED
    assert run.failure is not None
    assert run.failure.code is FailureCode.PROPOSAL_REJECTED
    assert run.failure.stage is Stage.CONSTRUCTION
    assert run.proposal is not None
    assert "unknown_property" in {r.code for r in run.rejections}


async def test_retrieval_failure_is_said_and_the_run_goes_on() -> None:
    retriever = Retriever(error=InvalidKnowledgeRequest(details={"field": "text", "reason": "required"}))
    run = await pipeline(SequencedLlm(output(claims=[]))).advance(a_run(), inputs(retriever=retriever))
    assert run.status is RunStatus.CANDIDATE_READY, (run.failure, run.rejections)
    assert any("could not be retrieved" in limit for limit in run.limitations)


async def test_a_passage_nothing_retrieved_cannot_be_cited() -> None:
    run = await pipeline(SequencedLlm(output())).advance(a_run(), inputs(retriever=Retriever()))
    assert run.status is RunStatus.FAILED  # output() cites kch_runbook, which nothing retrieved
    assert run.failure is not None
    assert run.failure.code is FailureCode.PROPOSAL_REJECTED
    assert "unknown_passage" in {r.code for r in run.rejections}


async def test_an_empty_set_is_unusable() -> None:
    run = await pipeline(SequencedLlm(output())).advance(a_run(), inputs(()))
    assert run.failure is not None
    assert run.failure.code is FailureCode.REQUIREMENTS_UNUSABLE


async def test_the_time_budget_stops_a_pass() -> None:
    agent = pipeline(SequencedLlm(output()), step=40)  # every reading of the clock is 40 s later
    run = await agent.advance(a_run(budget=Budget(max_seconds=60)), inputs())
    assert run.status is RunStatus.FAILED
    assert run.failure is not None
    assert run.failure.code is FailureCode.BUDGET_EXHAUSTED


class _BrokenAnalysis:
    def analyze(self, *args: object) -> None:
        raise RuntimeError("bug")


class _BrokenValidation:
    def validate(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("bug")


async def test_a_failing_analysis_engine_is_reported_not_hidden() -> None:
    engines = AgentEngines(ENGINES.validation, _BrokenAnalysis(), ENGINES.security, ENGINES.observability)  # type: ignore[arg-type]
    run = await pipeline(SequencedLlm(output()), engines=engines).advance(a_run(), inputs())
    assert run.status is RunStatus.CANDIDATE_READY
    reliability = next(r for r in run.reports if r.engine == "reliability")
    assert reliability.status is EngineStatus.FAILED
    assert reliability.findings == ()


async def test_without_validation_there_is_no_candidate() -> None:
    engines = AgentEngines(_BrokenValidation(), ENGINES.reliability, ENGINES.security, ENGINES.observability)  # type: ignore[arg-type]
    run = await pipeline(SequencedLlm(output()), engines=engines).advance(a_run(), inputs())
    assert run.status is RunStatus.FAILED
    assert run.failure is not None
    assert run.failure.code is FailureCode.ENGINE_ERROR
    assert run.candidate is None


async def test_a_finished_run_is_not_advanced() -> None:
    run = await pipeline(SequencedLlm(output())).advance(a_run(), inputs())
    with pytest.raises(InvalidAgentTransition):
        await pipeline(SequencedLlm(output())).advance(run, inputs())


async def test_the_same_inputs_give_the_same_candidate() -> None:
    first = await pipeline(SequencedLlm(output())).advance(a_run(), inputs())
    second = await pipeline(SequencedLlm(output())).advance(a_run(), inputs())
    assert first.candidate is not None
    assert second.candidate is not None
    assert first.candidate.content_hash == second.candidate.content_hash
    assert [r.to_dict() for r in first.reports] == [r.to_dict() for r in second.reports]


async def test_validation_blocking_is_read_only_from_an_evaluated_validation() -> None:
    run = await pipeline(SequencedLlm(output())).advance(a_run(), inputs())
    count = validation_blocking(run.reports)
    assert isinstance(count, int)
    assert count == next(r for r in run.reports if r.engine == "validation").summary["blocking"]
    assert validation_blocking(not_evaluated_reports()) is None
    assert validation_blocking((failed_report("validation"),)) is None  # never read as "none"
