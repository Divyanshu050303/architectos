"""The architecture workflow's domain contracts (Autonomous Architecture Workflow, phase 1): every valid
transition and the invalid ones the spec names; input, review and approval rules; bounded budgets with
ceilings; the closed tool registry with its side-effect policy; candidate lineage and immutability;
step identity for checkpoints and idempotent retries."""

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain.architecture_agent.proposals import Answer, ClarificationQuestion
from core.domain.architecture_agent.requests import BaseRevision
from core.domain.architecture_agent.results import EngineReport
from core.domain.architecture_agent.values import EngineStatus, QuestionKind
from core.domain.architecture_workflow.budget import (
    CEILINGS,
    WorkflowBudget,
    WorkflowUsage,
    blocking_limit,
)
from core.domain.architecture_workflow.candidates import FindingRef, WorkflowCandidate
from core.domain.architecture_workflow.errors import (
    InvalidWorkflowRecord,
    InvalidWorkflowRequest,
    InvalidWorkflowTransition,
    ToolNotAllowed,
)
from core.domain.architecture_workflow.goals import WorkflowGoal
from core.domain.architecture_workflow.steps import WorkflowStep, operation_key
from core.domain.architecture_workflow.tools import TOOLS, authorize
from core.domain.architecture_workflow.values import (
    AUTOMATIC,
    FAILURE_CLASS,
    Action,
    CandidateOrigin,
    CandidateStatus,
    FailureCode,
    InputKind,
    SideEffect,
    Stage,
    StepStatus,
    WorkflowStatus,
)
from core.domain.architecture_workflow.workflows import (
    MOVES,
    ApprovedRevision,
    ArchitectureWorkflow,
    InputRequest,
)

NOW = datetime(2026, 10, 3, tzinfo=UTC)
USER = uuid.uuid4()
S = WorkflowStatus
QUESTION = ClarificationQuestion(QuestionKind.MISSING_CONCERN, "What availability is required?")
OPTIONAL = ClarificationQuestion(QuestionKind.AMBIGUITY, "Is mobile in scope?", blocking=False)


def workflow(**overrides: Any) -> ArchitectureWorkflow:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "requested_by_user_id": USER,
        "requested_at": NOW,
        "goal": WorkflowGoal("A ride-sharing platform for 200K daily active users"),
    }
    return ArchitectureWorkflow(**(fields | overrides))


def running() -> ArchitectureWorkflow:
    return workflow().start(NOW)


def reviewable(*selected: uuid.UUID) -> ArchitectureWorkflow:
    return running().ready_for_review(selected or (uuid.uuid4(),), NOW)


def report(blocking: int | None) -> EngineReport:
    if blocking is None:
        return EngineReport("validation", EngineStatus.NOT_EVALUATED, limitations=("Not run.",))
    return EngineReport("validation", EngineStatus.EVALUATED, summary={"blocking": blocking})


def candidate(**overrides: Any) -> WorkflowCandidate:
    ir = overrides.pop("ir", ArchitectureIR("Rides"))
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workflow_id": uuid.uuid4(),
        "ordinal": 1,
        "origin": CandidateOrigin.AGENT,
        "reason": "The agent's design for the pinned requirement set.",
        "ir": ir,
        "content_hash": content_hash(ir),
        "created_at": NOW,
    }
    return WorkflowCandidate(**(fields | overrides))


# --- the state machine -------------------------------------------------------------------------------


def _approved() -> ArchitectureWorkflow:
    chosen = uuid.uuid4()
    return reviewable(chosen).approve(ApprovedRevision(chosen, uuid.uuid4(), 1), USER, NOW)


REACH: dict[WorkflowStatus, Callable[[], ArchitectureWorkflow]] = {
    S.QUEUED: workflow,
    S.RUNNING: running,
    S.NEEDS_INPUT: lambda: running().ask(InputRequest(InputKind.CLARIFICATION, questions=(QUESTION,)), NOW),
    S.REVIEW_READY: reviewable,
    S.APPROVED: _approved,
    S.REJECTED: lambda: reviewable().reject("Too costly to operate.", USER, NOW),
    S.CANCELLED: lambda: running().cancel(USER, NOW),
    S.FAILED: lambda: running().fail(FailureCode.LLM_UNAVAILABLE, "No model is configured.", NOW),
}


def _reach(status: WorkflowStatus) -> ArchitectureWorkflow:
    return REACH[status]()


def _move(flow: ArchitectureWorkflow, to: WorkflowStatus) -> ArchitectureWorkflow:
    chosen = flow.selected[0] if flow.selected else uuid.uuid4()
    moves = {
        S.QUEUED: lambda: (
            flow.release(NOW)
            if flow.status is S.RUNNING
            else flow.provide_input(USER, NOW, answers=(Answer(QUESTION.id, "99.9%", USER, NOW),))
        ),
        S.RUNNING: lambda: flow.start(NOW),
        S.NEEDS_INPUT: lambda: flow.ask(InputRequest(InputKind.CLARIFICATION, questions=(QUESTION,)), NOW),
        S.REVIEW_READY: lambda: flow.ready_for_review((chosen,), NOW),
        S.APPROVED: lambda: flow.approve(ApprovedRevision(chosen, uuid.uuid4(), 1), USER, NOW),
        S.REJECTED: lambda: flow.reject("No.", USER, NOW),
        S.CANCELLED: lambda: flow.cancel(USER, NOW),
        S.FAILED: lambda: flow.fail(FailureCode.ENGINE_ERROR, "An engine failed.", NOW),
    }
    return moves[to]()


VALID = [(a, b) for a, targets in MOVES.items() for b in sorted(targets)]
INVALID = [(a, b) for a in S for b in S if b not in MOVES.get(a, frozenset())]


@pytest.mark.parametrize(("start", "to"), VALID, ids=[f"{a}->{b}" for a, b in VALID])
def test_every_valid_transition(start: WorkflowStatus, to: WorkflowStatus) -> None:
    moved = _move(_reach(start), to)
    assert moved.status is to
    assert moved.history[-1].status is to
    assert moved.finished == (to in {S.APPROVED, S.REJECTED, S.CANCELLED, S.FAILED})


@pytest.mark.parametrize(("start", "to"), INVALID, ids=[f"{a}->{b}" for a, b in INVALID])
def test_every_other_transition_is_refused(start: WorkflowStatus, to: WorkflowStatus) -> None:
    with pytest.raises((InvalidWorkflowTransition, InvalidWorkflowRequest)):
        _move(_reach(start), to)


@pytest.mark.parametrize("start", [S.QUEUED, S.RUNNING, S.FAILED, S.CANCELLED, S.NEEDS_INPUT])
def test_nothing_reaches_approval_but_through_review(start: WorkflowStatus) -> None:
    """CREATED → APPROVED, RUNNING → APPROVED, FAILED → APPROVED, CANCELLED → APPROVED all fail."""
    with pytest.raises(InvalidWorkflowTransition):
        _reach(start).approve(ApprovedRevision(uuid.uuid4(), uuid.uuid4(), 1), USER, NOW)


def test_stages_move_only_while_running() -> None:
    assert running().at_stage(Stage.ANALYSIS).stage is Stage.ANALYSIS
    with pytest.raises(InvalidWorkflowTransition):
        workflow().at_stage(Stage.GENERATION)
    with pytest.raises(InvalidWorkflowTransition):
        reviewable().at_stage(Stage.GENERATION)


def test_inconsistent_workflows_are_refused() -> None:
    with pytest.raises(InvalidWorkflowRecord):
        workflow(status=S.NEEDS_INPUT)  # waiting, without saying for what
    with pytest.raises(InvalidWorkflowRecord):
        workflow(status=S.REVIEW_READY)  # nothing to review
    with pytest.raises(InvalidWorkflowRecord):
        workflow(status=S.FAILED, completed_at=NOW)  # failed, without why


def test_a_failure_says_its_class_and_stage() -> None:
    failed = running().at_stage(Stage.GENERATION).fail(FailureCode.LLM_TIMEOUT, "The model timed out.", NOW)
    assert failed.failure is not None
    assert failed.failure.to_dict() == {
        "code": "llm_timeout",
        "class": "ai",
        "message": "The model timed out.",
        "stage": "generation",
    }
    assert set(FAILURE_CLASS) == set(FailureCode)  # every failure is classified


# --- a person's input, review and decision ---------------------------------------------------------------


def test_confirming_requirements_needs_the_pinned_set() -> None:
    analysis, pinned = uuid.uuid4(), uuid.uuid4()
    waiting = running().ask(InputRequest(InputKind.CONFIRM_REQUIREMENTS, analysis), NOW)
    with pytest.raises(InvalidWorkflowRequest):
        waiting.provide_input(USER, NOW)
    resumed = waiting.provide_input(USER, NOW, requirement_set_id=pinned)
    assert (resumed.status, resumed.requirement_set_id, resumed.input_request) == (S.QUEUED, pinned, None)
    assert resumed.history[-1].user_id == USER


def test_clarification_needs_every_blocking_answer_and_nothing_else() -> None:
    waiting = running().ask(InputRequest(InputKind.CLARIFICATION, questions=(QUESTION, OPTIONAL)), NOW)
    with pytest.raises(InvalidWorkflowRequest):
        waiting.provide_input(USER, NOW, answers=(Answer(OPTIONAL.id, "No", USER, NOW),))
    with pytest.raises(InvalidWorkflowRequest):
        waiting.provide_input(USER, NOW, answers=(Answer("aq_unknown", "Yes", USER, NOW),))
    resumed = waiting.provide_input(USER, NOW, answers=(Answer(QUESTION.id, "99.9%", USER, NOW),))
    assert [a.question_id for a in resumed.answers] == [QUESTION.id]
    with pytest.raises(InvalidWorkflowRecord):
        InputRequest(InputKind.CLARIFICATION, questions=(OPTIONAL,))  # nothing blocking: nothing to wait for


def test_only_a_selected_candidate_can_be_approved() -> None:
    chosen = uuid.uuid4()
    flow = reviewable(chosen)
    with pytest.raises(InvalidWorkflowRequest):
        flow.approve(ApprovedRevision(uuid.uuid4(), uuid.uuid4(), 3), USER, NOW)
    approved = flow.approve(ApprovedRevision(chosen, uuid.uuid4(), 3), USER, NOW)
    assert approved.approved is not None
    assert approved.completed_at == NOW


def test_a_rejection_says_why() -> None:
    with pytest.raises(InvalidWorkflowRequest):
        reviewable().reject("   ", USER, NOW)
    assert reviewable().reject("  Too   costly ", USER, NOW).decision_reason == "Too costly"


def test_cancelling_keeps_what_was_done() -> None:
    flow = running().at_stage(Stage.ANALYSIS).counted(WorkflowUsage(llm_calls=2, tool_calls=5))
    cancelled = flow.cancel(USER, NOW)
    assert cancelled.usage == flow.usage
    assert cancelled.history[:-1] == flow.history


# --- goals ---------------------------------------------------------------------------------------------


def test_a_goal_is_cleaned_and_never_completed() -> None:
    goal = WorkflowGoal("  Rides  ", constraints=("AWS", "AWS"), base=BaseRevision(uuid.uuid4(), 2))
    assert (goal.objective, goal.constraints) == ("Rides", ("AWS",))
    assert goal.requirement_set_id is None  # nothing is invented
    request = goal.agent_request(uuid.uuid4())
    assert (request.objective, request.base) == ("Rides", goal.base)
    for bad in ({"objective": " "}, {"objective": "x" * 5000}, {"constraints": ("x" * 400,)}):
        with pytest.raises(InvalidWorkflowRequest):
            WorkflowGoal(**({"objective": "Rides"} | bad))


# --- budgets ---------------------------------------------------------------------------------------------


def test_budgets_are_bounded_by_ceilings_and_only_lowered() -> None:
    with pytest.raises(InvalidWorkflowRequest):
        WorkflowBudget(max_iterations=int(CEILINGS["max_iterations"]) + 1)
    with pytest.raises(InvalidWorkflowRecord):
        WorkflowBudget(max_llm_calls=0)
    budget = WorkflowBudget().lowered(max_iterations=1)
    assert budget.max_iterations == 1
    with pytest.raises(InvalidWorkflowRequest):
        budget.lowered(max_iterations=2)


@pytest.mark.parametrize(
    ("usage", "action", "elapsed", "limit"),
    [
        (WorkflowUsage(), Action.GENERATE_ARCHITECTURE, 0, None),
        (WorkflowUsage(), Action.VALIDATE_ARCHITECTURE, 1800, "max_seconds"),
        (WorkflowUsage(tool_calls=40), Action.VALIDATE_ARCHITECTURE, 0, "max_tool_calls"),
        (WorkflowUsage(llm_calls=11), Action.GENERATE_ALTERNATIVE, 0, "max_llm_calls"),  # needs room to retry
        (WorkflowUsage(llm_calls=11), Action.VALIDATE_ARCHITECTURE, 0, None),  # no model
        (WorkflowUsage(input_tokens=240_000), Action.GENERATE_ARCHITECTURE, 0, "max_input_tokens"),
        (WorkflowUsage(candidates=6), Action.GENERATE_ALTERNATIVE, 0, "max_candidates"),
        (WorkflowUsage(iterations=3), Action.GENERATE_ALTERNATIVE, 0, "max_iterations"),
        (WorkflowUsage(retrievals=4), Action.RETRIEVE_KNOWLEDGE, 0, "max_retrievals"),
        (WorkflowUsage(simulations=3), Action.RUN_SIMULATION, 0, "max_simulations"),
    ],
)
def test_every_expensive_action_is_checked_before_it_runs(
    usage: WorkflowUsage, action: Action, elapsed: float, limit: str | None
) -> None:
    assert blocking_limit(WorkflowBudget(), usage, action, elapsed) == limit


def test_unreported_tokens_stay_unknown() -> None:
    assert WorkflowUsage(input_tokens=None).plus(WorkflowUsage(input_tokens=10)).input_tokens is None


# --- the tool registry -----------------------------------------------------------------------------------


def test_every_action_is_registered_and_none_changes_the_canonical_architecture() -> None:
    assert set(TOOLS) == set(Action)
    for spec in TOOLS.values():
        assert spec.side_effect in AUTOMATIC, spec.action
        assert spec.description
        assert spec.stages
    names = {a.value for a in Action}
    for forbidden in ("execute_code", "execute_shell", "execute_sql", "browse", "approve", "deploy"):
        assert not any(forbidden in n for n in names)
    assert {SideEffect.CANONICAL_MUTATION, SideEffect.EXTERNAL_SIDE_EFFECT}.isdisjoint(AUTOMATIC)


def test_actions_run_only_when_registered_running_and_in_their_stage() -> None:
    allowed = authorize(Action.VALIDATE_ARCHITECTURE, S.RUNNING, Stage.VALIDATION)
    assert allowed.action is Action.VALIDATE_ARCHITECTURE
    refusals = [
        ("execute_shell", S.RUNNING, Stage.ANALYSIS, "unknown_action"),  # a model-made name
        (Action.VALIDATE_ARCHITECTURE, S.NEEDS_INPUT, Stage.VALIDATION, "not_running"),
        (Action.VALIDATE_ARCHITECTURE, S.REVIEW_READY, Stage.VALIDATION, "not_running"),
        (Action.GENERATE_ALTERNATIVE, S.RUNNING, Stage.GENERATION, "wrong_stage"),
    ]
    for action, status, stage, reason in refusals:
        with pytest.raises(ToolNotAllowed) as refused:
            authorize(action, status, stage)
        assert refused.value.details["reason"] == reason


# --- candidates ------------------------------------------------------------------------------------------


def test_the_first_candidate_is_the_agents_and_improvements_have_lineage() -> None:
    first = candidate()
    trigger = FindingRef("capacity", "capacity:db:connections", "high", ("db",))
    improved = candidate(
        workflow_id=first.workflow_id, ordinal=2, origin=CandidateOrigin.RULE, parent_id=first.id,
        trigger=trigger, rule="add_read_replicas", reason="Answers the database bottleneck.",
    )  # fmt: skip
    assert (improved.parent_id, improved.trigger) == (first.id, trigger)
    bad: list[dict[str, Any]] = [
        {"ordinal": 2},  # no parent
        {"parent_id": uuid.uuid4()},  # the first has none
        {"origin": CandidateOrigin.RULE},  # the first is the agent's
        {
            "ordinal": 2,
            "parent_id": first.id,
            "origin": CandidateOrigin.RULE,
            "trigger": trigger,
        },  # no rule id
        {"ordinal": 2, "parent_id": first.id, "origin": CandidateOrigin.RULE, "rule": "r"},  # no trigger
        {"content_hash": "0" * 64},  # not this architecture
    ]
    for overrides in bad:
        with pytest.raises(InvalidWorkflowRecord):
            candidate(**overrides)


def test_validation_decides_the_status_and_nothing_else() -> None:
    generated = candidate()
    with pytest.raises(InvalidWorkflowTransition):
        generated.validated()  # not evaluated yet: unknown, never valid
    assert generated.with_report(report(0)).validated().status is CandidateStatus.VALIDATED
    rejected = generated.with_report(report(2)).validated()
    assert rejected.status is CandidateStatus.REJECTED
    with pytest.raises(InvalidWorkflowTransition):
        rejected.moved(CandidateStatus.SELECTED_FOR_REVIEW)  # kept, never reviewed
    with pytest.raises(InvalidWorkflowRecord):
        candidate(status=CandidateStatus.VALIDATED)  # without validation's report
    with pytest.raises(InvalidWorkflowRecord):
        candidate(status=CandidateStatus.VALIDATED, reports=(report(None),))


def test_a_candidates_architecture_never_changes() -> None:
    first = candidate().with_report(report(0)).validated()
    analyzed = first.with_report(EngineReport("security", EngineStatus.EVALUATED))
    assert analyzed.ir is first.ir
    assert analyzed.content_hash == first.content_hash
    assert {r.engine for r in analyzed.reports} == {"validation", "security"}
    selected = analyzed.moved(CandidateStatus.SELECTED_FOR_REVIEW).moved(CandidateStatus.ACCEPTED)
    assert selected.status is CandidateStatus.ACCEPTED
    with pytest.raises(InvalidWorkflowTransition):
        selected.moved(CandidateStatus.SUPERSEDED)


# --- steps (checkpoints) ---------------------------------------------------------------------------------


def step(**overrides: Any) -> WorkflowStep:
    flow_id = overrides.pop("workflow_id", uuid.uuid4())
    action = overrides.pop("action", Action.VALIDATE_ARCHITECTURE)
    subject = overrides.pop("subject", "c1")
    iteration = overrides.pop("iteration", 0)
    fields: dict[str, Any] = {
        "key": operation_key(flow_id, iteration, action, subject),
        "workflow_id": flow_id,
        "ordinal": 1,
        "iteration": iteration,
        "action": action,
        "stage": Stage.VALIDATION,
        "status": StepStatus.COMPLETED,
        "started_at": NOW,
        "completed_at": NOW + timedelta(seconds=1),
        "subject": subject,
    }
    return WorkflowStep(**(fields | overrides))


def test_an_operation_has_one_stable_identity() -> None:
    flow = uuid.uuid4()
    same = operation_key(flow, 1, Action.RUN_COST_ANALYSIS, "c2")
    assert same == operation_key(flow, 1, Action.RUN_COST_ANALYSIS, "c2")
    assert same != operation_key(flow, 2, Action.RUN_COST_ANALYSIS, "c2")
    assert same != operation_key(flow, 1, Action.RUN_COST_ANALYSIS, "c3")
    with pytest.raises(InvalidWorkflowRecord):
        replace(step(workflow_id=flow), key=same)  # a key that is not its own


def test_a_step_says_how_it_ended() -> None:
    assert step().done
    failed = step(status=StepStatus.FAILED, error="llm_timeout", retryable=True)
    assert not failed.done  # may be attempted again under the same key
    assert step(status=StepStatus.FAILED, error="engine_error").done
    for bad in (
        {"status": StepStatus.FAILED},  # without why
        {"error": "x"},  # completed, with an error
        {"retryable": True},  # only a failure is retried
        {"stage": Stage.ANALYSIS},  # validation runs in its own stage
        {"outputs": {"text": "x" * 500}},  # references, never content
        {"completed_at": NOW - timedelta(seconds=1)},
    ):
        with pytest.raises(InvalidWorkflowRecord):
            step(**bad)
