"""The workflow controller and planner (Autonomous Architecture Workflow, phase 2), with scripted
executors and an in-memory store: requirements are confirmed by a person before design; findings drive
improvement candidates that keep their parents; validation blocks are answered by the agent; budgets
stop the workflow at review or failure, never past a limit; a crash is retried once and completed steps
are never repeated; a revoked permission or a cancellation stops it with nothing more executed."""

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain.architecture_agent.results import AgentFinding, EngineReport
from core.domain.architecture_agent.values import EngineStatus
from core.domain.architecture_workflow.budget import WorkflowBudget, WorkflowUsage
from core.domain.architecture_workflow.candidates import WorkflowCandidate
from core.domain.architecture_workflow.controller import WorkflowController
from core.domain.architecture_workflow.goals import WorkflowGoal
from core.domain.architecture_workflow.planner import Inputs, reviewable
from core.domain.architecture_workflow.ports import StepContext, StepOutcome, WorkflowSnapshot
from core.domain.architecture_workflow.steps import WorkflowStep
from core.domain.architecture_workflow.values import (
    Action,
    CandidateOrigin,
    CandidateStatus,
    FailureCode,
    InputKind,
    StepStatus,
    WorkflowStatus,
)
from core.domain.architecture_workflow.workflows import ArchitectureWorkflow, InputRequest
from core.domain.organizations.permissions import Permission
from tests.unit.architecture_ir.builders import node

A = Action
START = datetime(2026, 10, 3, tzinfo=UTC)
USER = uuid.uuid4()
SINGLE_DB = AgentFinding("reliability", "single-database", "high", "One database replica.", ("db",))


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@dataclass
class Store:
    workflow: ArchitectureWorkflow
    candidates: dict[uuid.UUID, WorkflowCandidate] = field(default_factory=dict)
    steps: list[WorkflowStep] = field(default_factory=list)
    inputs: Inputs = field(default_factory=Inputs)
    cancel_after: int | None = None  # a person cancels after this many commits
    commits: int = 0

    async def snapshot(self, workflow_id: uuid.UUID) -> WorkflowSnapshot | None:
        ordered = tuple(sorted(self.candidates.values(), key=lambda c: c.ordinal))
        return WorkflowSnapshot(self.workflow, ordered, tuple(self.steps), self.inputs)

    async def commit(
        self,
        expected: ArchitectureWorkflow,
        workflow: ArchitectureWorkflow,
        candidates: tuple[WorkflowCandidate, ...],
        step: WorkflowStep | None,
    ) -> bool:
        if self.workflow != expected:
            return False
        self.workflow = workflow
        for c in candidates:
            self.candidates[c.id] = c
        if step is not None:
            self.steps.append(step)
        self.commits += 1
        if self.cancel_after is not None and self.commits >= self.cancel_after:
            self.workflow = self.workflow.cancel(USER, START)
        return True


@dataclass
class Permissions:
    granted: bool = True

    async def allowed(self, workflow: ArchitectureWorkflow, permission: Permission) -> bool:
        return self.granted


def ir(*names: str) -> ArchitectureIR:
    return ArchitectureIR("Rides", nodes=tuple(node(n, NodeKind.SERVICE) for n in names))


def built(context: StepContext, architecture: ArchitectureIR, **fields: Any) -> WorkflowCandidate:
    return WorkflowCandidate(
        id=uuid.uuid4(), workflow_id=context.workflow.id, ordinal=context.next_ordinal,
        ir=architecture, content_hash=content_hash(architecture), created_at=context.now, **fields,
    )  # fmt: skip


@dataclass
class Script:
    """Scripted executors: what each action does, and what was executed."""

    blocking: dict[int, int] = field(default_factory=dict)  # validation blocking findings by ordinal
    findings: dict[int, tuple[AgentFinding, ...]] = field(default_factory=lambda: {1: (SINGLE_DB,)})
    generation_error: str | None = None
    crash_once: set[Action] = field(default_factory=set)
    executed: list[Action] = field(default_factory=list)

    def executors(self) -> dict[Action, Any]:
        return {action: _Executor(self, action) for action in Action}


@dataclass
class _Executor:
    script: Script
    action: Action

    async def execute(self, context: StepContext) -> StepOutcome:  # noqa: PLR0911 - one per action
        script, action = self.script, self.action
        script.executed.append(action)
        if action in script.crash_once:
            script.crash_once.discard(action)
            raise ConnectionError("database went away")
        done = StepStatus.COMPLETED
        target = context.candidate(context.decision.candidate_id)
        match action:
            case A.ANALYZE_REQUIREMENTS:
                return StepOutcome(
                    done, requirement_analysis_id=uuid.uuid4(), usage=WorkflowUsage(llm_calls=1)
                )
            case A.REQUEST_CLARIFICATION:
                analysis = context.workflow.requirement_analysis_id
                return StepOutcome(done, ask=InputRequest(InputKind.CONFIRM_REQUIREMENTS, analysis))
            case A.RETRIEVE_KNOWLEDGE:
                return StepOutcome(done, usage=WorkflowUsage(retrievals=2), outputs={"passages": 3})
            case A.GENERATE_ARCHITECTURE:
                if script.generation_error:
                    return StepOutcome(StepStatus.FAILED, error=script.generation_error, note="No model.")
                first = built(
                    context, ir("api", "db"), origin=CandidateOrigin.AGENT, reason="The agent's design."
                )
                return StepOutcome(done, candidates=(first,), usage=WorkflowUsage(llm_calls=1, candidates=1))
            case A.VALIDATE_ARCHITECTURE:
                assert target is not None
                blocking = script.blocking.get(target.ordinal, 0)
                report = EngineReport("validation", EngineStatus.EVALUATED, summary={"blocking": blocking})
                return StepOutcome(done, candidates=(target.with_report(report).validated(),))
            case A.GENERATE_ALTERNATIVE:
                assert target is not None
                trigger = context.decision.trigger
                assert trigger is not None
                by_rule = trigger.engine != "validation"
                origin = CandidateOrigin.RULE if by_rule else CandidateOrigin.AGENT_REVISION
                improved = built(
                    context, ir("api", "db", f"replica-{context.next_ordinal}"), origin=origin,
                    parent_id=target.id, trigger=trigger, rule="add_replica" if by_rule else None,
                    reason=context.decision.reason,
                )  # fmt: skip
                usage = WorkflowUsage(candidates=1, llm_calls=0 if by_rule else 1)
                return StepOutcome(done, candidates=(improved,), usage=usage)
            case A.COMPARE_CANDIDATES:
                return StepOutcome(done, outputs={"diff_id": str(uuid.uuid4())})
            case A.PREPARE_REVIEW:
                chosen = reviewable(context.candidates)
                moved = tuple(c.moved(CandidateStatus.SELECTED_FOR_REVIEW) for c in chosen)
                return StepOutcome(done, candidates=moved, selected=tuple(c.id for c in chosen))
        assert target is not None
        engine = action.value.removeprefix("run_").removesuffix("_analysis")
        findings = script.findings.get(target.ordinal, ()) if engine == "reliability" else ()
        report = EngineReport(engine, EngineStatus.EVALUATED, findings=findings)
        return StepOutcome(done, candidates=(target.with_report(report),))


def workflow(**overrides: Any) -> ArchitectureWorkflow:
    goal = WorkflowGoal("A ride-sharing platform", requirement_set_id=overrides.pop("set_id", uuid.uuid4()))
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "requested_by_user_id": USER,
        "requested_at": START,
        "goal": goal,
        "requirement_set_id": goal.requirement_set_id,
    }
    return ArchitectureWorkflow(**(fields | overrides)).start(START)


async def run(store: Store, script: Script, permissions: Permissions | None = None) -> ArchitectureWorkflow:
    controller = WorkflowController(store, script.executors(), permissions or Permissions(), clock=Clock())
    finished = await controller.advance(store.workflow.id)
    assert finished is not None
    return finished


def candidates(store: Store) -> list[WorkflowCandidate]:
    return sorted(store.candidates.values(), key=lambda c: c.ordinal)


# --- the whole workflow ----------------------------------------------------------------------------------


async def test_a_person_confirms_requirements_before_any_design() -> None:
    store, script = Store(workflow(set_id=None, requirement_set_id=None)), Script()
    paused = await run(store, script)
    assert paused.status is WorkflowStatus.NEEDS_INPUT
    assert paused.input_request is not None
    assert paused.input_request.kind is InputKind.CONFIRM_REQUIREMENTS
    assert script.executed == [A.ANALYZE_REQUIREMENTS, A.REQUEST_CLARIFICATION]  # no design yet
    store.workflow = paused.provide_input(USER, START, requirement_set_id=uuid.uuid4()).start(START)
    reviewed = await run(store, script)
    assert reviewed.status is WorkflowStatus.REVIEW_READY
    assert script.executed.count(A.ANALYZE_REQUIREMENTS) == 1  # never repeated after the pause


async def test_a_finding_drives_an_improvement_that_keeps_its_parent() -> None:
    store, script = Store(workflow()), Script()
    reviewed = await run(store, script)
    first, second = candidates(store)
    assert (first.origin, second.origin) == (CandidateOrigin.AGENT, CandidateOrigin.RULE)
    assert second.parent_id == first.id
    assert second.trigger is not None
    assert (second.trigger.engine, second.trigger.finding_id) == ("reliability", "single-database")
    assert first.content_hash == content_hash(ir("api", "db"))  # the parent is unchanged
    assert reviewed.selected == (second.id, first.id)  # newest first; both reviewable
    assert {c.status for c in (first, second)} == {CandidateStatus.SELECTED_FOR_REVIEW}
    assert reviewed.iteration == 1
    order = [s.action for s in store.steps]
    assert order[:3] == [A.RETRIEVE_KNOWLEDGE, A.GENERATE_ARCHITECTURE, A.VALIDATE_ARCHITECTURE]
    assert order[-1] is A.PREPARE_REVIEW
    assert all(s.status is StepStatus.COMPLETED for s in store.steps)
    assert len({s.key for s in store.steps}) == len(store.steps)  # each operation once


async def test_validation_blocks_are_answered_by_the_agent_and_kept() -> None:
    store, script = Store(workflow()), Script(blocking={1: 2}, findings={})
    reviewed = await run(store, script)
    first, second = candidates(store)
    assert first.status is CandidateStatus.REJECTED  # kept, never deleted
    assert second.origin is CandidateOrigin.AGENT_REVISION
    assert reviewed.selected == (second.id,)


async def test_no_valid_candidate_stops_with_why() -> None:
    store = Store(workflow(budget=WorkflowBudget(max_iterations=1)))
    failed = await run(store, Script(blocking={1: 1, 2: 1}, findings={}))
    assert failed.status is WorkflowStatus.FAILED
    assert failed.failure is not None
    assert failed.failure.code is FailureCode.NO_VALID_CANDIDATE
    assert len(store.candidates) == 2


async def test_a_generation_failure_is_said_never_invented() -> None:
    store, script = Store(workflow()), Script(generation_error="llm_unavailable")
    failed = await run(store, script)
    assert failed.failure is not None
    assert failed.failure.code is FailureCode.LLM_UNAVAILABLE
    assert store.candidates == {}


# --- budgets ---------------------------------------------------------------------------------------------


async def test_no_iterations_means_review_after_the_first_candidate() -> None:
    store, script = Store(workflow(budget=WorkflowBudget(max_iterations=0))), Script()
    reviewed = await run(store, script)
    assert reviewed.status is WorkflowStatus.REVIEW_READY
    assert A.GENERATE_ALTERNATIVE not in script.executed


async def test_a_limit_reached_with_a_valid_candidate_goes_to_review() -> None:
    store, script = Store(workflow(budget=WorkflowBudget(max_tool_calls=5))), Script()
    reviewed = await run(store, script)
    assert reviewed.status is WorkflowStatus.REVIEW_READY
    assert any("max_tool_calls" in note for note in reviewed.limitations)
    assert len(script.executed) <= 6  # five actions, then the review


async def test_an_unanswered_finding_at_the_iteration_limit_is_said() -> None:
    store = Store(workflow(budget=WorkflowBudget(max_iterations=0)))
    reviewed = await run(store, Script())  # the first candidate has a high reliability finding
    assert reviewed.status is WorkflowStatus.REVIEW_READY
    assert len(store.candidates) == 1
    assert any("max_iterations" in note for note in reviewed.limitations)


async def test_a_limit_reached_with_nothing_valid_fails() -> None:
    store = Store(workflow(budget=WorkflowBudget(max_tool_calls=3)))
    failed = await run(store, Script(blocking={1: 1}))
    assert failed.failure is not None
    assert failed.failure.code is FailureCode.BUDGET_EXHAUSTED


async def test_the_time_limit_stops_it() -> None:
    late = workflow()
    store = Store(replace(late, started_at=START - timedelta(hours=2)))
    failed = await run(store, Script())
    assert failed.failure is not None
    assert failed.failure.code is FailureCode.TIMED_OUT


# --- crashes, retries and resumption ---------------------------------------------------------------------


async def test_a_crash_is_retried_once_and_completed_steps_never_repeat() -> None:
    store, script = Store(workflow()), Script(crash_once={A.RUN_SECURITY_ANALYSIS})
    reviewed = await run(store, script)
    assert reviewed.status is WorkflowStatus.REVIEW_READY
    crashed = [s for s in store.steps if s.status is StepStatus.FAILED]
    assert [(s.action, s.error, s.retryable, s.attempt) for s in crashed] == [
        (A.RUN_SECURITY_ANALYSIS, "infrastructure_error", True, 1)
    ]
    retried = [s for s in store.steps if s.key == crashed[0].key]
    assert [s.attempt for s in retried] == [1, 2]
    assert script.executed.count(A.GENERATE_ARCHITECTURE) == 1


async def test_resuming_continues_after_the_last_completed_step() -> None:
    store, script = Store(workflow()), Script()
    controller = WorkflowController(store, script.executors(), Permissions(), clock=Clock())
    await controller.advance(store.workflow.id, max_turns=3)  # a worker stops after three steps
    before = list(script.executed)
    assert before == [A.RETRIEVE_KNOWLEDGE, A.GENERATE_ARCHITECTURE, A.VALIDATE_ARCHITECTURE]
    store.workflow = store.workflow.release(START).start(START)  # another worker takes it
    await controller.advance(store.workflow.id)
    assert script.executed[:3] == before
    assert script.executed.count(A.GENERATE_ARCHITECTURE) == 1
    assert store.workflow.status is WorkflowStatus.REVIEW_READY


async def test_a_second_failure_is_final() -> None:
    script = Script()
    store = Store(workflow(budget=WorkflowBudget(max_iterations=0)))
    executors = script.executors()

    class Broken:
        async def execute(self, context: StepContext) -> StepOutcome:
            raise ConnectionError

    executors[A.RUN_SECURITY_ANALYSIS] = Broken()
    controller = WorkflowController(store, executors, Permissions(), clock=Clock())
    reviewed = await controller.advance(store.workflow.id)
    assert reviewed is not None
    attempts = [s for s in store.steps if s.action is A.RUN_SECURITY_ANALYSIS]
    assert [(s.attempt, s.retryable) for s in attempts] == [(1, True), (2, False)]
    assert reviewed.status is WorkflowStatus.REVIEW_READY  # the other analyses still count


# --- permissions and cancellation ------------------------------------------------------------------------


async def test_a_revoked_permission_stops_it_before_anything_runs() -> None:
    store, script = Store(workflow()), Script()
    failed = await run(store, script, Permissions(granted=False))
    assert failed.failure is not None
    assert failed.failure.code is FailureCode.PERMISSION_DENIED
    assert script.executed == []


async def test_a_cancellation_stops_every_future_step_and_keeps_history() -> None:
    store, script = Store(workflow(), cancel_after=2), Script()
    stopped = await run(store, script)
    assert stopped.status is WorkflowStatus.CANCELLED
    assert len(script.executed) == 2
    assert len(store.steps) == 2  # what was done is kept


# --- what runs on each candidate ------------------------------------------------------------------------

OPTIONAL = {A.RUN_CAPACITY_ANALYSIS, A.RUN_COST_ANALYSIS, A.RUN_SIMULATION}


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        (Inputs(), set()),
        (Inputs(workload=True), {A.RUN_CAPACITY_ANALYSIS}),
        (Inputs(workload=True, pricing=True, scenario=True), OPTIONAL),
    ],
)
async def test_engines_run_only_with_their_inputs(inputs: Inputs, expected: set[Action]) -> None:
    store, script = Store(workflow(budget=WorkflowBudget(max_iterations=0)), inputs=inputs), Script()
    await run(store, script)
    assert set(script.executed) & OPTIONAL == expected
    always = {A.RUN_RELIABILITY_ANALYSIS, A.RUN_SECURITY_ANALYSIS, A.RUN_OBSERVABILITY_ANALYSIS}
    assert always <= set(script.executed)
