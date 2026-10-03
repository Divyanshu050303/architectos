"""The workflow's real executors (Autonomous Architecture Workflow, phase 3): the controller and planner
with the architecture agent's pipeline (a scripted model), the deterministic engines, the evolution
rules and the architecture diff — only the project's records are faked. A goal becomes a validated,
analyzed candidate and a review package; capacity runs only on a named workload; a model that is not
configured, a retrieval that fails and a requirement set with gaps are each handled as stated."""

import copy
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ai.agents.architecture_agent import ArchitectureProposalAgent
from ai.evaluation.architecture_agent import PROJECT, _requirement
from core.domain.architecture_agent.proposals import Answer
from core.domain.architecture_diff.ports import CapacityInputs
from core.domain.architecture_workflow.budget import WorkflowBudget, WorkflowUsage
from core.domain.architecture_workflow.candidates import FindingRef, WorkflowCandidate
from core.domain.architecture_workflow.controller import WorkflowController
from core.domain.architecture_workflow.planner import Inputs, Next
from core.domain.architecture_workflow.ports import (
    RequirementAnalysis,
    StepContext,
    WorkflowInputs,
)
from core.domain.architecture_workflow.values import (
    Action,
    CandidateOrigin,
    CandidateStatus,
    FailureCode,
    InputKind,
    Stage,
    StepStatus,
    WorkflowStatus,
)
from core.domain.architecture_workflow.workflows import ArchitectureWorkflow
from core.domain.knowledge.retrieval import RetrievalQuery, RetrievalResult
from core.domain.requirements.entities import Requirement
from core.domain.requirements.planning import build_planning_input
from engines.architecture_agent.factory import NotConfigured
from engines.architecture_agent.orchestrator import AgentEngines, ArchitectureAgentPipeline
from engines.architecture_diff.engine import DeterministicDiffEngine
from engines.architecture_workflow.executors import (
    AgentStep,
    GenerateAlternative,
    WorkflowEngines,
    build_executors,
)
from engines.evolution.service import DeterministicEvolutionEngine
from engines.simulation.service import DeterministicSimulationEngine
from persistence.component_catalog import default_catalog
from tests.integration.api.agent_support import AGENT_OUTPUT
from tests.unit.ai.fakes import ScriptedLlm
from tests.unit.architecture_diff.test_diff_impact import ENGINES
from tests.unit.architecture_workflow.test_workflow_controller import Permissions, Store, workflow
from tests.unit.simulation.test_simulation_engine import workload

AT = datetime(2026, 10, 3, tzinfo=UTC)
THROUGHPUT = {
    "type": "capacity",
    "category": "throughput",
    "metric": "requests_per_second",
    "operator": ">=",
    "value": "500",
    "unit": "requests/second",
}
AVAILABILITY = {
    "type": "availability",
    "category": "availability",
    "metric": "availability",
    "operator": ">=",
    "value": "99.9",
    "unit": "%",
}
CATALOG = default_catalog()
# The agent's design with a browser in front and single replicas declared: single points of failure.
REPLICATED: dict[str, Any] = copy.deepcopy(AGENT_OUTPUT)
REPLICATED["nodes"].insert(
    0, {"id": "web", "kind": "client", "name": "Web", "rationale": "People order", "confidence": 0.9}
)
REPLICATED["connections"].insert(
    0,
    {"id": "web-api", "source": "web", "target": "orders-api", "kind": "request", "rationale": "Orders",
     "protocol": "https", "confidence": 0.9},
)  # fmt: skip
for _node in REPLICATED["nodes"]:
    if _node["id"] in {"orders-db", "orders-api"}:
        _node["configuration"] = [{"property": "replicas", "value": 1}]
WORKFLOW_ENGINES = WorkflowEngines(
    ENGINES.validation,
    ENGINES.reliability,
    ENGINES.security,
    ENGINES.observability,
    ENGINES.capacity,
    ENGINES.cost,
    DeterministicSimulationEngine(),
    DeterministicEvolutionEngine(),
    DeterministicDiffEngine(ENGINES),
)


def requirements(*specs: dict[str, Any]) -> tuple[Requirement, ...]:
    return tuple(_requirement(n, spec) for n, spec in enumerate(specs, start=1))


@dataclass
class Loader:
    pinned: tuple[Requirement, ...] = field(default_factory=lambda: requirements(THROUGHPUT, AVAILABILITY))
    capacity: CapacityInputs | None = None

    async def load(self, workflow: ArchitectureWorkflow) -> WorkflowInputs:
        planning = build_planning_input(PROJECT, list(self.pinned))
        return WorkflowInputs(planning, self.pinned, capacity=self.capacity)


@dataclass
class Knowledge:
    broken: bool = False
    queries: list[RetrievalQuery] = field(default_factory=list)

    async def retrieve(self, workflow: ArchitectureWorkflow, query: RetrievalQuery) -> RetrievalResult:
        self.queries.append(query)
        if self.broken:
            raise ConnectionError("search is down")
        return RetrievalResult((), 0)


@dataclass
class Analyzer:
    found: int = 2

    async def analyze(self, workflow: ArchitectureWorkflow) -> RequirementAnalysis:
        return RequirementAnalysis(uuid.uuid4(), self.found, WorkflowUsage(llm_calls=1))


def pipeline(llm: ScriptedLlm | None = None, *, configured: bool = True) -> ArchitectureAgentPipeline:
    engines = AgentEngines(ENGINES.validation, ENGINES.reliability, ENGINES.security, ENGINES.observability)
    proposer = ArchitectureProposalAgent(llm or ScriptedLlm(AGENT_OUTPUT)) if configured else NotConfigured()
    return ArchitectureAgentPipeline(proposer, engines, CATALOG, clock=lambda: AT)


def controller(
    store: Store, *, loader: Loader | None = None, knowledge: Knowledge | None = None, **agent: Any
) -> WorkflowController:
    executors = build_executors(
        engines=WORKFLOW_ENGINES, pipeline=pipeline(**agent), loader=loader or Loader(),
        analyzer=Analyzer(), knowledge=knowledge or Knowledge(),
    )  # fmt: skip
    return WorkflowController(store, executors, Permissions(), clock=lambda: AT)


async def advance(store: Store, **kwargs: Any) -> ArchitectureWorkflow:
    done = await controller(store, **kwargs).advance(store.workflow.id)
    assert done is not None
    return done


def ordered(store: Store) -> list[WorkflowCandidate]:
    return sorted(store.candidates.values(), key=lambda c: c.ordinal)


# --- a goal to a review package -------------------------------------------------------------------------


async def test_a_goal_becomes_a_validated_analyzed_candidate_for_review() -> None:
    store = Store(workflow(budget=WorkflowBudget(max_iterations=0)))
    reviewed = await advance(store)
    assert reviewed.status is WorkflowStatus.REVIEW_READY, (reviewed.failure, store.steps)
    [first] = ordered(store)
    assert first.origin is CandidateOrigin.AGENT
    assert first.status is CandidateStatus.SELECTED_FOR_REVIEW
    assert first.ir.node("orders-db") is not None  # the agent's design, as canonical IR
    engines = {r.engine for r in first.reports}
    assert engines == {"validation", "reliability", "security", "observability"}  # no workload, no pricing
    assert first.blocking == 0
    assert reviewed.selected == (first.id,)
    assert reviewed.usage.llm_calls == 1
    assert reviewed.usage.candidates == 1
    said = " ".join(reviewed.limitations)  # what was not run is said, never reported as nothing found
    assert "Capacity was not analyzed" in said
    assert "Cost was not analyzed" in said
    assert "Nothing was simulated" in said
    compared = next(s for s in store.steps if s.action is Action.COMPARE_CANDIDATES)
    assert compared.status is StepStatus.SKIPPED  # a first design has nothing to compare with


async def test_findings_drive_rule_improvements_kept_with_their_parents() -> None:
    """Single replicas are single points of failure (high, reliability): the evolution rules answer each
    one with a configuration change of its parent — no model call — and each is compared with it."""
    store = Store(workflow(budget=WorkflowBudget(max_iterations=3)))
    reviewed = await advance(store, llm=ScriptedLlm(REPLICATED))
    assert reviewed.status is WorkflowStatus.REVIEW_READY
    first, second, third = ordered(store)
    assert (first.origin, second.origin, third.origin) == (
        CandidateOrigin.AGENT,
        CandidateOrigin.RULE,
        CandidateOrigin.RULE,
    )
    assert (second.parent_id, third.parent_id) == (first.id, second.id)
    assert {second.rule, third.rule} == {"add-replica"}
    triggers = {c.trigger.elements for c in (second, third) if c.trigger}
    assert triggers == {("orders-db",), ("orders-api",)}  # each finding answered once

    def replicas(candidate: WorkflowCandidate) -> object:
        db = candidate.ir.node("orders-db")
        assert db is not None
        return db.configuration.values["replicas"]

    assert replicas(first) == 1  # the parent is unchanged
    assert replicas(second) == 2
    assert reviewed.usage.llm_calls == 1  # the first design only
    compared = [
        s.outputs
        for s in store.steps
        if s.action is Action.COMPARE_CANDIDATES and s.status is StepStatus.COMPLETED
    ]
    assert [(c["changes"], c["resolved"] >= 1) for c in compared] == [(1, True), (1, True)]
    assert set(reviewed.selected) == {first.id, second.id, third.id}
    assert len({s.key for s in store.steps}) == len(store.steps)  # nothing done twice across rounds


async def test_capacity_runs_only_on_a_named_workload() -> None:
    named = CapacityInputs(uuid.uuid4(), workload())
    store = Store(workflow(budget=WorkflowBudget(max_iterations=0)), inputs=Inputs(workload=True))
    await advance(store, loader=Loader(capacity=named))
    [first] = ordered(store)
    capacity = first.report("capacity")
    assert capacity is not None
    assert any(str(named.analysis_id) in note for note in capacity.limitations)


# --- what is said, never invented -----------------------------------------------------------------------


async def test_without_a_model_the_workflow_fails_llm_unavailable() -> None:
    store = Store(workflow())
    failed = await advance(store, configured=False)
    assert failed.status is WorkflowStatus.FAILED
    assert failed.failure is not None
    assert failed.failure.code is FailureCode.LLM_UNAVAILABLE
    assert store.candidates == {}


async def test_a_retrieval_failure_is_said_and_the_design_proceeds() -> None:
    knowledge = Knowledge(broken=True)
    store = Store(workflow(budget=WorkflowBudget(max_iterations=0)))
    reviewed = await advance(store, knowledge=knowledge)
    assert reviewed.status is WorkflowStatus.REVIEW_READY
    retrieval = next(s for s in store.steps if s.action is Action.RETRIEVE_KNOWLEDGE)
    assert retrieval.status is StepStatus.SKIPPED
    assert retrieval.note is not None


async def test_gaps_in_the_requirements_wait_for_a_person_then_resume() -> None:
    loader = Loader(pinned=requirements(THROUGHPUT))  # no availability: the agent asks
    store = Store(workflow(budget=WorkflowBudget(max_iterations=0)))
    paused = await advance(store, loader=loader)
    assert paused.status is WorkflowStatus.NEEDS_INPUT
    assert paused.input_request is not None
    assert paused.input_request.kind is InputKind.CLARIFICATION
    person = paused.requested_by_user_id
    answers = tuple(
        Answer(q.id, "99.9% each month", person, AT) for q in paused.input_request.questions if q.blocking
    )
    store.workflow = paused.provide_input(person, AT, answers=answers).start(AT)
    reviewed = await advance(store, loader=loader)
    assert reviewed.status is WorkflowStatus.REVIEW_READY, reviewed.failure
    assert len(store.candidates) == 1


async def test_an_improvement_no_rule_answers_goes_to_the_agent_and_never_repeats_a_design() -> None:
    store = Store(workflow(budget=WorkflowBudget(max_iterations=0)))
    await advance(store)
    [first] = ordered(store)
    trigger = FindingRef(
        "capacity", "orders-api:cpu", "critical", ("orders-api",)
    )  # no evolution rule source
    decision = Next(
        Action.GENERATE_ALTERNATIVE, Stage.ITERATION, "k", 0, candidate_id=first.id, trigger=trigger,
        reason="A critical capacity finding.",
    )  # fmt: skip
    context = StepContext(store.workflow, (first,), decision, 1, AT, 2)
    revised = GenerateAlternative(
        WORKFLOW_ENGINES, AgentStep(pipeline(ScriptedLlm(REPLICATED)), Loader(), Knowledge()), Loader()
    )
    outcome = await revised.execute(context)
    [improved] = outcome.candidates
    assert improved.origin is CandidateOrigin.AGENT_REVISION
    assert (improved.parent_id, improved.trigger) == (first.id, trigger)
    assert outcome.usage.llm_calls == 1
    same = GenerateAlternative(WORKFLOW_ENGINES, AgentStep(pipeline(), Loader(), Knowledge()), Loader())
    repeated = await same.execute(context)  # the agent answers with the design it already gave
    assert (repeated.status, repeated.error, repeated.candidates) == (StepStatus.SKIPPED, "no_new_design", ())
