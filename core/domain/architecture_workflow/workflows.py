"""An architecture workflow: a goal carried, by the server, through requirements, knowledge,
generation, validation, analysis, iteration and comparison to a reviewable package — then a person's
decision. The workflow owns no architecture: approval goes through the architecture workflow.

Lifecycle (any other move is refused — there is no way to ``approved`` but through ``review_ready``):

    queued ─▶ running ─▶ review_ready ─▶ approved | rejected     (a person decides)
      ▲         │  │          └──────▶ cancelled
      │         │  └▶ needs_input ─(a person's input)─▶ queued
      │         ├▶ failed       (with a code, its class and the stage)
      │         └▶ cancelled    (also from queued and needs_input)
      └─────────┘ (released by a worker, e.g. when its lease expired)

- **Input.** ``needs_input`` always says what is needed: requirement candidates of a stored analysis
  to confirm (then a pinned requirement set), or blocking questions to answer.
- **Review.** ``review_ready`` always names the candidates selected for review; ``approved`` names the
  candidate and the exact revision it became; ``rejected`` says why.
- Every status change is kept, in order, with who (when a person) and when.
"""

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from core.domain.architecture_agent.proposals import Answer, ClarificationQuestion

from .budget import WorkflowBudget, WorkflowUsage
from .errors import InvalidWorkflowRequest, InvalidWorkflowTransition
from .goals import WorkflowGoal
from .values import (
    FAILURE_CLASS,
    TERMINAL,
    FailureClass,
    FailureCode,
    InputKind,
    Stage,
    WorkflowStatus,
    check,
    count,
    items,
    text,
    texts,
)

S = WorkflowStatus
MOVES: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    S.QUEUED: frozenset({S.RUNNING, S.CANCELLED, S.FAILED}),
    S.RUNNING: frozenset({S.QUEUED, S.NEEDS_INPUT, S.REVIEW_READY, S.FAILED, S.CANCELLED}),
    S.NEEDS_INPUT: frozenset({S.QUEUED, S.CANCELLED, S.FAILED}),
    S.REVIEW_READY: frozenset({S.APPROVED, S.REJECTED, S.CANCELLED}),
}
WITH_REVIEW = frozenset({S.REVIEW_READY, S.APPROVED, S.REJECTED})
MAX_HISTORY = 200
MAX_LIMITATIONS = 50
MAX_SELECTED = 8
MAX_REASON = 2000


@dataclass(frozen=True, slots=True)
class InputRequest:
    """What a person must provide before the workflow continues."""

    kind: InputKind
    analysis_id: uuid.UUID | None = None  # the requirement analysis whose candidates to confirm
    questions: tuple[ClarificationQuestion, ...] = ()

    def __post_init__(self) -> None:
        confirming = self.kind is InputKind.CONFIRM_REQUIREMENTS
        check(
            [
                None if isinstance(self.kind, InputKind) else "input.kind",
                "input.analysis_id" if confirming != isinstance(self.analysis_id, uuid.UUID) else None,
                items(self.questions, ClarificationQuestion, "input.questions", 10),
                "input.questions"
                if self.kind is InputKind.CLARIFICATION and not any(q.blocking for q in self.questions)
                else None,
            ]
        )


@dataclass(frozen=True, slots=True)
class WorkflowFailure:
    code: FailureCode
    message: str  # for a person: never a provider's raw error, a prompt or retrieved text
    stage: Stage

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.code, FailureCode) else "failure.code",
                text(self.message, "failure.message", 500),
                None if isinstance(self.stage, Stage) else "failure.stage",
            ]
        )

    @property
    def failure_class(self) -> FailureClass:
        return FAILURE_CLASS[self.code]

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "class": self.failure_class.value,
            "message": self.message,
            "stage": self.stage.value,
        }


@dataclass(frozen=True, slots=True)
class ApprovedRevision:
    """The candidate a person approved and the exact revision it became."""

    candidate_id: uuid.UUID
    architecture_id: uuid.UUID
    number: int


@dataclass(frozen=True, slots=True)
class StatusEvent:
    status: WorkflowStatus
    stage: Stage
    at: datetime
    user_id: uuid.UUID | None = None  # set when a person moved it

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "stage": self.stage.value,
            "at": self.at.isoformat(),
            "user_id": str(self.user_id) if self.user_id else None,
        }


@dataclass(frozen=True, slots=True)
class ArchitectureWorkflow:
    id: uuid.UUID
    project_id: uuid.UUID
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    goal: WorkflowGoal
    budget: WorkflowBudget = field(default_factory=WorkflowBudget)
    status: WorkflowStatus = WorkflowStatus.QUEUED
    stage: Stage = Stage.INTAKE
    iteration: int = 0
    usage: WorkflowUsage = field(default_factory=WorkflowUsage)
    requirement_analysis_id: uuid.UUID | None = None
    requirement_set_id: uuid.UUID | None = None
    input_request: InputRequest | None = None
    answers: tuple[Answer, ...] = ()
    selected: tuple[uuid.UUID, ...] = ()  # the candidates in the review package
    approved: ApprovedRevision | None = None
    decision_reason: str | None = None
    failure: WorkflowFailure | None = None
    history: tuple[StatusEvent, ...] = ()
    limitations: tuple[str, ...] = ()  # what it left out or could not do, said (never silent)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        status = self.status
        check(
            [
                None if isinstance(self.goal, WorkflowGoal) else "workflow.goal",
                None if isinstance(self.budget, WorkflowBudget) else "workflow.budget",
                None if isinstance(status, WorkflowStatus) else "workflow.status",
                None if isinstance(self.stage, Stage) else "workflow.stage",
                count(self.iteration, "workflow.iteration"),
                None if isinstance(self.usage, WorkflowUsage) else "workflow.usage",
                "workflow.input_request"
                if (status is S.NEEDS_INPUT) != (self.input_request is not None)
                else None,
                items(self.answers, Answer, "workflow.answers", 50),
                items(self.selected, uuid.UUID, "workflow.selected", MAX_SELECTED),
                "workflow.selected" if status in WITH_REVIEW and not self.selected else None,
                "workflow.approved" if (status is S.APPROVED) != (self.approved is not None) else None,
                "workflow.approved"
                if self.approved is not None and self.approved.candidate_id not in self.selected
                else None,
                "workflow.decision_reason"
                if (status is S.REJECTED) != (self.decision_reason is not None)
                else None,
                "workflow.failure" if (status is S.FAILED) != (self.failure is not None) else None,
                items(self.history, StatusEvent, "workflow.history", MAX_HISTORY),
                texts(self.limitations, "workflow.limitations", 500),
                "workflow.limitations" if len(self.limitations) > MAX_LIMITATIONS else None,
                "workflow.completed_at" if (status in TERMINAL) != (self.completed_at is not None) else None,
            ]
        )

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL

    def _moved(
        self, status: WorkflowStatus, at: datetime, user_id: uuid.UUID | None = None, **changes: Any
    ) -> ArchitectureWorkflow:
        if status not in MOVES.get(self.status, frozenset()):
            raise InvalidWorkflowTransition(details={"from": self.status.value, "to": status.value})
        event = StatusEvent(status, changes.get("stage", self.stage), at, user_id)
        completed = at if status in TERMINAL else None
        history = (*self.history, event)[-MAX_HISTORY:]
        return replace(self, status=status, history=history, completed_at=completed, **changes)

    # --- the controller's moves ------------------------------------------------------------------

    def start(self, at: datetime) -> ArchitectureWorkflow:
        """A worker took it."""
        return self._moved(S.RUNNING, at, started_at=self.started_at or at)

    def release(self, at: datetime) -> ArchitectureWorkflow:
        """Back to the queue: its worker stopped (a lease expired); the next one resumes it."""
        return self._moved(S.QUEUED, at)

    def at_stage(self, stage: Stage) -> ArchitectureWorkflow:
        if self.status is not S.RUNNING:
            raise InvalidWorkflowTransition(details={"from": self.status.value, "to": stage.value})
        return replace(self, stage=stage)

    def counted(self, usage: WorkflowUsage) -> ArchitectureWorkflow:
        return replace(self, usage=self.usage.plus(usage))

    def next_iteration(self) -> ArchitectureWorkflow:
        return replace(self, iteration=self.iteration + 1)

    def with_requirements(
        self, *, analysis_id: uuid.UUID | None = None, set_id: uuid.UUID | None = None
    ) -> ArchitectureWorkflow:
        return replace(
            self,
            requirement_analysis_id=analysis_id or self.requirement_analysis_id,
            requirement_set_id=set_id or self.requirement_set_id,
        )

    def noting(self, *limitations: str) -> ArchitectureWorkflow:
        kept = tuple(dict.fromkeys((*self.limitations, *limitations)))[:MAX_LIMITATIONS]
        return replace(self, limitations=kept)

    def ask(self, request: InputRequest, at: datetime) -> ArchitectureWorkflow:
        return self._moved(S.NEEDS_INPUT, at, input_request=request)

    def ready_for_review(self, selected: tuple[uuid.UUID, ...], at: datetime) -> ArchitectureWorkflow:
        if not selected:
            raise InvalidWorkflowTransition(details={"from": self.status.value, "to": S.REVIEW_READY.value})
        return self._moved(S.REVIEW_READY, at, selected=tuple(dict.fromkeys(selected)), stage=Stage.REVIEW)

    def fail(self, code: FailureCode, message: str, at: datetime) -> ArchitectureWorkflow:
        failure = WorkflowFailure(code, message, self.stage)
        return self._moved(S.FAILED, at, failure=failure, input_request=None)

    # --- a person's moves ------------------------------------------------------------------------

    def provide_input(
        self,
        user_id: uuid.UUID,
        at: datetime,
        *,
        requirement_set_id: uuid.UUID | None = None,
        answers: tuple[Answer, ...] = (),
    ) -> ArchitectureWorkflow:
        """The confirmed requirement set, or answers to every blocking question; back to the queue."""
        request = self.input_request
        if self.status is not S.NEEDS_INPUT or request is None:
            raise InvalidWorkflowTransition(details={"from": self.status.value, "to": S.QUEUED.value})
        if request.kind is InputKind.CONFIRM_REQUIREMENTS:
            if requirement_set_id is None or answers:
                raise InvalidWorkflowRequest(details={"field": "requirement_set_id", "reason": "required"})
            return self._moved(
                S.QUEUED, at, user_id, input_request=None, requirement_set_id=requirement_set_id
            )
        asked = {q.id for q in request.questions if q.blocking}
        known = {q.id for q in request.questions}
        given = {a.question_id for a in answers}
        if requirement_set_id is not None or not asked <= given or not given <= known:
            raise InvalidWorkflowRequest(details={"field": "answers", "reason": "unknown_or_missing"})
        return self._moved(S.QUEUED, at, user_id, input_request=None, answers=(*self.answers, *answers))

    def approve(self, approved: ApprovedRevision, user_id: uuid.UUID, at: datetime) -> ArchitectureWorkflow:
        if self.status is S.REVIEW_READY and approved.candidate_id not in self.selected:
            raise InvalidWorkflowRequest(details={"field": "candidate_id", "reason": "not_selected"})
        return self._moved(S.APPROVED, at, user_id, approved=approved, stage=Stage.DECISION)

    def reject(self, reason: str, user_id: uuid.UUID, at: datetime) -> ArchitectureWorkflow:
        clean = " ".join(reason.split()) if isinstance(reason, str) else ""
        if not clean or len(clean) > MAX_REASON:
            problem = "required" if not clean else "too_long"
            raise InvalidWorkflowRequest(details={"field": "reason", "reason": problem})
        return self._moved(S.REJECTED, at, user_id, decision_reason=clean, stage=Stage.DECISION)

    def cancel(self, user_id: uuid.UUID, at: datetime) -> ArchitectureWorkflow:
        """Stops every future stage; what was done is kept."""
        return self._moved(S.CANCELLED, at, user_id, input_request=None)
