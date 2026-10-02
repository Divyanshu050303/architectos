"""An architecture agent run: one request carried through the fixed stages to a candidate a person
decides on — or to a stated failure, or a question for a person.

Lifecycle (any other move is refused):

    queued ─▶ running ─▶ candidate_ready ─▶ accepted | rejected      (a person decides)
               │  ▲   └▶ awaiting_clarification ─(answers)─┘
               │  └──────────────────────────────┘
               ├▶ failed       (with a code and the stage: never a candidate)
               └▶ cancelled    (also from queued and awaiting_clarification)

- A **candidate** exists only once the run is ``candidate_ready`` (and after its decision); a failed
  or cancelled run never has one, so it can never be accepted.
- **Accepting** records the exact revision the candidate became; **rejecting** records why.
- Every status change is kept, in order, with who (when a person) and when.
- What the model was asked and answered is not stored: the prompt version, the model, the usage, the
  validated proposal and a SHA-256 and size of the raw output are.
"""

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from .errors import InvalidAgentRequest, InvalidAgentTransition
from .proposals import Answer, ClarificationQuestion, Proposal
from .requests import AgentRequest, AgentUsage, Budget
from .results import Candidate, EngineReport, Rejection
from .values import FINGERPRINT, FailureCode, RunStatus, Stage, check, code, count, items, text

S = RunStatus
MOVES: dict[RunStatus, frozenset[RunStatus]] = {
    S.QUEUED: frozenset({S.RUNNING, S.FAILED, S.CANCELLED}),
    S.RUNNING: frozenset({S.AWAITING_CLARIFICATION, S.CANDIDATE_READY, S.FAILED, S.CANCELLED}),
    S.AWAITING_CLARIFICATION: frozenset({S.RUNNING, S.FAILED, S.CANCELLED}),
    S.CANDIDATE_READY: frozenset({S.ACCEPTED, S.REJECTED}),
}
WITH_CANDIDATE = frozenset({S.CANDIDATE_READY, S.ACCEPTED, S.REJECTED})
MAX_HISTORY = 50


def _unanswered(
    questions: tuple[ClarificationQuestion, ...], answers: tuple[Answer, ...]
) -> tuple[ClarificationQuestion, ...]:
    answered = {a.question_id for a in answers}
    return tuple(q for q in questions if q.blocking and q.id not in answered)


@dataclass(frozen=True, slots=True)
class RunFailure:
    code: FailureCode
    message: str  # for a person; never a provider's raw error, a prompt or retrieved text
    stage: Stage

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.code, FailureCode) else "failure.code",
                text(self.message, "failure.message", 500),
                None if isinstance(self.stage, Stage) else "failure.stage",
            ]
        )

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message, "stage": self.stage.value}


@dataclass(frozen=True, slots=True)
class RawOutput:
    """The model's raw output is not kept — only that it existed: its SHA-256 and size."""

    sha256: str
    bytes: int

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.sha256, str) and FINGERPRINT.fullmatch(self.sha256) else "raw.sha256",
                count(self.bytes, "raw.bytes"),
            ]
        )


@dataclass(frozen=True, slots=True)
class AcceptedRevision:
    architecture_id: uuid.UUID
    number: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class StatusEvent:
    status: RunStatus
    stage: Stage
    at: datetime
    user_id: uuid.UUID | None = None  # set when a person moved it (answer, cancel, accept, reject)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "stage": self.stage.value,
            "at": self.at.isoformat(),
            "user_id": str(self.user_id) if self.user_id else None,
        }


@dataclass(frozen=True, slots=True)
class AgentRun:
    id: uuid.UUID
    project_id: uuid.UUID
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    request: AgentRequest
    budget: Budget = field(default_factory=Budget)
    status: RunStatus = RunStatus.QUEUED
    stage: Stage = Stage.INTAKE
    usage: AgentUsage = field(default_factory=AgentUsage)
    model: str | None = None  # provider/model, once a model was called
    prompt_version: str | None = None
    questions: tuple[ClarificationQuestion, ...] = ()
    answers: tuple[Answer, ...] = ()
    proposal: Proposal | None = None
    rejections: tuple[Rejection, ...] = ()
    candidate: Candidate | None = None
    reports: tuple[EngineReport, ...] = ()
    raw_output: RawOutput | None = None
    failure: RunFailure | None = None
    accepted: AcceptedRevision | None = None
    decision_reason: str | None = None
    history: tuple[StatusEvent, ...] = ()
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        waiting_without_question = self.status is S.AWAITING_CLARIFICATION and not self.unanswered
        check(
            [
                None if isinstance(self.request, AgentRequest) else "run.request",
                None if isinstance(self.status, RunStatus) else "run.status",
                items(self.questions, ClarificationQuestion, "run.questions", 50),
                items(self.answers, Answer, "run.answers", 50),
                items(self.rejections, Rejection, "run.rejections", 100),
                items(self.reports, EngineReport, "run.reports", 10),
                "run.candidate" if (self.status in WITH_CANDIDATE) != (self.candidate is not None) else None,
                "run.failure" if (self.status is S.FAILED) != (self.failure is not None) else None,
                "run.accepted" if (self.status is S.ACCEPTED) != (self.accepted is not None) else None,
                "run.questions" if waiting_without_question else None,
                text(self.model, "run.model", 128, required=False),
                code(self.prompt_version, "run.prompt_version", required=False),
                text(self.decision_reason, "run.decision_reason", 2000, required=False),
                "run.history" if len(self.history) > MAX_HISTORY else None,
            ]
        )

    @property
    def unanswered(self) -> tuple[ClarificationQuestion, ...]:
        return _unanswered(self.questions, self.answers)

    def _allow(self, to: RunStatus) -> None:
        if to not in MOVES.get(self.status, frozenset()):
            raise InvalidAgentTransition(details={"from": self.status.value, "to": to.value})

    def _move(
        self, to: RunStatus, at: datetime, user_id: uuid.UUID | None = None, **changes: Any
    ) -> AgentRun:
        self._allow(to)
        event = StatusEvent(to, changes.get("stage", self.stage), at, user_id)
        if to in {S.CANDIDATE_READY, S.FAILED, S.CANCELLED}:
            changes["completed_at"] = at
        return replace(self, status=to, history=(*self.history, event), **changes)

    def start(self, at: datetime) -> AgentRun:
        return self._move(S.RUNNING, at, stage=Stage.INTERPRETATION)

    def at_stage(self, stage: Stage) -> AgentRun:
        if self.status is not S.RUNNING:
            raise InvalidAgentTransition(details={"from": self.status.value, "to": stage.value})
        return replace(self, stage=stage)

    def ask(self, questions: tuple[ClarificationQuestion, ...], at: datetime) -> AgentRun:
        """Blocking gaps: the run waits for a person. A question already answered is not asked again."""
        self._allow(S.AWAITING_CLARIFICATION)
        known = tuple({q.id: q for q in (*self.questions, *questions)}.values())
        if not _unanswered(known, self.answers):
            raise InvalidAgentRequest(details={"field": "questions", "reason": "none_blocking"})
        return self._move(S.AWAITING_CLARIFICATION, at, questions=known)

    def answer(self, answers: tuple[Answer, ...], user_id: uuid.UUID, at: datetime) -> AgentRun:
        """Every blocking question answered: the same run continues, with the answers as facts."""
        if self.status is not S.AWAITING_CLARIFICATION:
            raise InvalidAgentTransition(details={"from": self.status.value, "to": S.RUNNING.value})
        asked = {q.id for q in self.questions}
        if any(a.question_id not in asked for a in answers):
            raise InvalidAgentRequest(details={"field": "answers", "reason": "unknown_question"})
        merged = tuple({a.question_id: a for a in (*self.answers, *answers)}.values())
        if _unanswered(self.questions, merged):
            raise InvalidAgentRequest(details={"field": "answers", "reason": "blocking_questions_unanswered"})
        return self._move(S.RUNNING, at, user_id, stage=Stage.RETRIEVAL, answers=merged)

    def ready(
        self,
        proposal: Proposal,
        candidate: Candidate,
        reports: tuple[EngineReport, ...],
        at: datetime,
        rejections: tuple[Rejection, ...] = (),
    ) -> AgentRun:
        if not any(r.engine == "validation" for r in reports):
            raise InvalidAgentRequest(details={"field": "reports", "reason": "validation_required"})
        return self._move(
            S.CANDIDATE_READY, at, stage=Stage.REVIEW, proposal=proposal, candidate=candidate,
            reports=reports, rejections=rejections,
        )  # fmt: skip

    def fail(
        self,
        failure: RunFailure,
        at: datetime,
        *,
        proposal: Proposal | None = None,
        rejections: tuple[Rejection, ...] = (),
    ) -> AgentRun:
        return self._move(
            S.FAILED, at, stage=failure.stage, failure=failure, proposal=proposal, rejections=rejections
        )

    def cancel(self, user_id: uuid.UUID, at: datetime) -> AgentRun:
        return self._move(S.CANCELLED, at, user_id)

    def accept(self, revision: AcceptedRevision, user_id: uuid.UUID, at: datetime) -> AgentRun:
        self._allow(S.ACCEPTED)
        if self.candidate is None or revision.content_hash != self.candidate.content_hash:
            raise InvalidAgentRequest(details={"field": "content_hash", "reason": "not_the_candidate"})
        return self._move(S.ACCEPTED, at, user_id, stage=Stage.DECISION, accepted=revision)

    def reject(self, reason: str, user_id: uuid.UUID, at: datetime) -> AgentRun:
        return self._move(S.REJECTED, at, user_id, stage=Stage.DECISION, decision_reason=reason)

    def with_model(
        self, model: str, prompt_version: str, usage: AgentUsage, raw: RawOutput | None
    ) -> AgentRun:
        return replace(self, model=model, prompt_version=prompt_version, usage=usage, raw_output=raw)
