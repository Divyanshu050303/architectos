"""Architecture workflows over HTTP, typed field by field.

A workflow carries a goal to a review package; nothing in it is an architecture until a person approves
one candidate. What is not known is ``null`` — token counts a provider did not report, an engine that
was not run — never 0. Prompts, retrieved text and a model's raw output are never returned.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from core.architecture_ir.serialization import to_dict
from core.domain.architecture_agent.requests import MAX_CONTEXT, MAX_ITEMS, MAX_OBJECTIVE, BaseRevision
from core.domain.architecture_workflow.budget import CEILINGS
from core.domain.architecture_workflow.candidates import WorkflowCandidate
from core.domain.architecture_workflow.goals import WorkflowGoal
from core.domain.architecture_workflow.repository import WorkflowListing
from core.domain.architecture_workflow.steps import WorkflowStep
from core.domain.architecture_workflow.values import (
    Action,
    CandidateOrigin,
    CandidateStatus,
    FailureClass,
    FailureCode,
    InputKind,
    Stage,
    StepStatus,
    WorkflowStatus,
)
from core.domain.architecture_workflow.workflow_service import (
    ApprovedCandidate,
    WorkflowDetail,
    approval_blocks,
)
from core.domain.architecture_workflow.workflows import ArchitectureWorkflow

from .architecture_agent import (
    AnswerModel,
    AnswerResponse,
    BaseRevisionModel,
    EngineReportResponse,
    EvidenceResponse,
    Fingerprint,
    Item,
    QuestionResponse,
)
from .common import ApiModel, RequestModel
from .simulation import ScenarioInput

# --- requests ------------------------------------------------------------------------------------


class WorkflowBudgetModel(RequestModel):
    """Lowers the workflow's limits; never raises them above the configured ones."""

    max_iterations: int | None = Field(default=None, ge=0, le=CEILINGS["max_iterations"])
    max_llm_calls: int | None = Field(default=None, ge=1, le=CEILINGS["max_llm_calls"])
    max_tool_calls: int | None = Field(default=None, ge=1, le=CEILINGS["max_tool_calls"])
    max_retrievals: int | None = Field(default=None, ge=0, le=CEILINGS["max_retrievals"])
    max_candidates: int | None = Field(default=None, ge=1, le=CEILINGS["max_candidates"])
    max_simulations: int | None = Field(default=None, ge=0, le=CEILINGS["max_simulations"])
    max_input_tokens: int | None = Field(default=None, ge=1000, le=CEILINGS["max_input_tokens"])
    max_seconds: float | None = Field(default=None, ge=60, le=CEILINGS["max_seconds"])

    def limits(self) -> dict[str, Any]:
        return {name: value for name, value in self.model_dump(by_alias=False).items() if value is not None}


class WorkflowRequest(RequestModel):
    objective: Annotated[str, Field(min_length=1, max_length=MAX_OBJECTIVE)] = Field(
        description="What the system should do, in your own words."
    )
    constraints: Annotated[list[Item], Field(max_length=MAX_ITEMS)] = Field(
        default_factory=list, description="Hard constraints, kept as written."
    )
    preferences: Annotated[list[Item], Field(max_length=MAX_ITEMS)] = Field(
        default_factory=list, description="Soft preferences: never turned into constraints."
    )
    exclusions: Annotated[list[Item], Field(max_length=MAX_ITEMS)] = Field(
        default_factory=list, description="What is out of scope."
    )
    context: Annotated[str | None, Field(max_length=MAX_CONTEXT)] = None
    requirement_set_id: uuid.UUID | None = Field(
        default=None,
        description="Design against this pinned set. Without one, the goal is analyzed by the requirements "
        "engine and the workflow waits for you to confirm a requirement set.",
    )
    base: BaseRevisionModel | None = Field(
        default=None, description="Iterate on exactly this revision; approval then revises it."
    )
    capacity_analysis_id: uuid.UUID | None = Field(
        default=None,
        description="A capacity analysis of the base: its workload is reused for every candidate.",
    )
    cost_analysis_id: uuid.UUID | None = Field(
        default=None, description="A cost analysis of the base: its pricing is reused for every candidate."
    )
    scenario: ScenarioInput | None = Field(default=None, description="Simulated on every candidate.")
    budget: WorkflowBudgetModel | None = None

    def to_domain(self) -> tuple[WorkflowGoal, dict[str, Any]]:
        base = self.base
        goal = WorkflowGoal(
            self.objective,
            tuple(self.constraints),
            tuple(self.preferences),
            tuple(self.exclusions),
            self.context,
            BaseRevision(base.architecture_id, base.revision_number) if base else None,
            self.requirement_set_id,
            self.capacity_analysis_id,
            self.cost_analysis_id,
            self.scenario.to_domain() if self.scenario else None,
        )
        return goal, self.budget.limits() if self.budget else {}


class WorkflowInputRequest(RequestModel):
    """Exactly one: the requirement set you confirmed, or answers to every blocking question."""

    requirement_set_id: uuid.UUID | None = None
    answers: Annotated[list[AnswerModel], Field(max_length=50)] = Field(default_factory=list)


class WorkflowRejectRequest(RequestModel):
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class WorkflowApproveRequest(RequestModel):
    candidate_id: uuid.UUID
    candidate_content_hash: Fingerprint = Field(description="The candidate as reviewed: its contentHash.")
    name: Annotated[str | None, Field(min_length=1, max_length=100)] = Field(
        default=None, description="A new architecture's name (default: the candidate's)."
    )


# --- responses -----------------------------------------------------------------------------------


class UsageResponse(ApiModel):
    iterations: int
    llm_calls: int
    input_tokens: int | None = Field(description="As providers reported them; null: not reported.")
    output_tokens: int | None
    tool_calls: int
    retrievals: int
    candidates: int
    simulations: int


class FindingRefResponse(ApiModel):
    engine: str
    finding_id: str
    severity: str
    elements: list[str]


class ReportStatusResponse(ApiModel):
    engine: str
    status: str
    findings: int


class ApprovabilityResponse(ApiModel):
    approvable: bool
    reason: str | None = Field(
        description="not_selected, validation_blocking or validation_unknown; or the workflow's status "
        "when it is not review_ready."
    )


def _approvability(workflow: ArchitectureWorkflow, candidate: WorkflowCandidate) -> ApprovabilityResponse:
    reason: str | None
    if workflow.status is not WorkflowStatus.REVIEW_READY:
        reason = workflow.status.value
    elif candidate.id not in workflow.selected or candidate.status is not CandidateStatus.SELECTED_FOR_REVIEW:
        reason = "not_selected"
    else:
        reason = approval_blocks(candidate)
    return ApprovabilityResponse(approvable=reason is None, reason=reason)


class CandidateSummary(ApiModel):
    id: uuid.UUID
    ordinal: int
    origin: CandidateOrigin
    status: CandidateStatus
    reason: str
    parent_id: uuid.UUID | None
    trigger: FindingRefResponse | None = Field(description="The parent's finding this candidate answers.")
    rule: str | None = Field(description="The deterministic evolution rule that proposed it.")
    agent_run_id: uuid.UUID | None
    content_hash: str = Field(description="Name it when approving: the candidate as reviewed.")
    blocking: int | None = Field(description="Validation's blocking findings; null: not validated.")
    reports: list[ReportStatusResponse]
    approvability: ApprovabilityResponse
    created_at: datetime

    @classmethod
    def fields_of(cls, workflow: ArchitectureWorkflow, c: WorkflowCandidate) -> dict[str, Any]:
        return {
            "id": c.id,
            "ordinal": c.ordinal,
            "origin": c.origin,
            "status": c.status,
            "reason": c.reason,
            "parent_id": c.parent_id,
            "trigger": FindingRefResponse(**c.trigger.to_dict()) if c.trigger else None,
            "rule": c.rule,
            "agent_run_id": c.agent_run_id,
            "content_hash": c.content_hash,
            "blocking": c.blocking,
            "reports": [
                ReportStatusResponse(engine=r.engine, status=r.status.value, findings=len(r.findings))
                for r in c.reports
            ],
            "approvability": _approvability(workflow, c),
            "created_at": c.created_at,
        }

    @classmethod
    def of(cls, workflow: ArchitectureWorkflow, candidate: WorkflowCandidate) -> CandidateSummary:
        return cls(**cls.fields_of(workflow, candidate))


class WorkflowCandidateResponse(CandidateSummary):
    architecture: dict[str, Any] = Field(description="Canonical Architecture IR.")
    full_reports: list[EngineReportResponse] = Field(description="The engines' own reports; never a score.")
    assumptions: list[str]
    rationale: list[str] = Field(
        description="The proposer's stated reasons (a model's or a rule's), unverified."
    )
    evidence: list[EvidenceResponse]
    limitations: list[str]

    @classmethod
    def of(cls, workflow: ArchitectureWorkflow, candidate: WorkflowCandidate) -> WorkflowCandidateResponse:
        return cls(
            **cls.fields_of(workflow, candidate),
            architecture=to_dict(candidate.ir),
            full_reports=[EngineReportResponse.of(r) for r in candidate.reports],
            assumptions=list(candidate.assumptions),
            rationale=list(candidate.rationale),
            evidence=[EvidenceResponse(**e.to_dict()) for e in candidate.evidence],
            limitations=list(candidate.limitations),
        )


class StepResponse(ApiModel):
    ordinal: int
    iteration: int
    action: Action
    stage: Stage
    status: StepStatus
    subject: str
    attempt: int
    candidate_id: uuid.UUID | None
    outputs: dict[str, Any] = Field(description="References only: identifiers, counts, codes.")
    usage: UsageResponse
    error: str | None
    retryable: bool
    note: str | None
    started_at: datetime
    completed_at: datetime

    @classmethod
    def of(cls, step: WorkflowStep) -> StepResponse:
        return cls(
            ordinal=step.ordinal,
            iteration=step.iteration,
            action=step.action,
            stage=step.stage,
            status=step.status,
            subject=step.subject,
            attempt=step.attempt,
            candidate_id=step.candidate_id,
            outputs=dict(step.outputs),
            usage=UsageResponse(**step.usage.to_dict()),
            error=step.error,
            retryable=step.retryable,
            note=step.note,
            started_at=step.started_at,
            completed_at=step.completed_at,
        )


class InputNeededResponse(ApiModel):
    kind: InputKind
    analysis_id: uuid.UUID | None = Field(description="The requirement analysis whose candidates to confirm.")
    questions: list[QuestionResponse]


class WorkflowFailureResponse(ApiModel):
    code: FailureCode
    failure_class: FailureClass
    message: str
    stage: Stage


class ApprovedRevisionResponse(ApiModel):
    candidate_id: uuid.UUID
    architecture_id: uuid.UUID
    revision_number: int


class StatusEventResponse(ApiModel):
    status: WorkflowStatus
    stage: Stage
    at: datetime
    user_id: uuid.UUID | None


class WorkflowResponse(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: WorkflowStatus
    stage: Stage
    iteration: int
    goal: dict[str, Any]
    budget: dict[str, Any]
    usage: UsageResponse
    requirement_analysis_id: uuid.UUID | None
    requirement_set_id: uuid.UUID | None
    input_needed: InputNeededResponse | None
    answers: list[AnswerResponse]
    selected: list[uuid.UUID] = Field(description="The candidates of the review package.")
    approved: ApprovedRevisionResponse | None
    decision_reason: str | None
    failure: WorkflowFailureResponse | None
    history: list[StatusEventResponse]
    limitations: list[str]
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    candidates: list[CandidateSummary]
    steps: list[StepResponse]

    @classmethod
    def of(cls, detail: WorkflowDetail) -> WorkflowResponse:
        flow = detail.workflow
        request, approved, failure = flow.input_request, flow.approved, flow.failure
        answered = {a.question_id for a in flow.answers}
        return cls(
            id=flow.id,
            project_id=flow.project_id,
            status=flow.status,
            stage=flow.stage,
            iteration=flow.iteration,
            goal=flow.goal.to_dict(),
            budget=flow.budget.to_dict(),
            usage=UsageResponse(**flow.usage.to_dict()),
            requirement_analysis_id=flow.requirement_analysis_id,
            requirement_set_id=flow.requirement_set_id,
            input_needed=InputNeededResponse(
                kind=request.kind,
                analysis_id=request.analysis_id,
                questions=[QuestionResponse.of(q, answered) for q in request.questions],
            )
            if request
            else None,
            answers=[AnswerResponse(**a.to_dict()) for a in flow.answers],
            selected=list(flow.selected),
            approved=ApprovedRevisionResponse(
                candidate_id=approved.candidate_id,
                architecture_id=approved.architecture_id,
                revision_number=approved.number,
            )
            if approved
            else None,
            decision_reason=flow.decision_reason,
            failure=WorkflowFailureResponse(
                code=failure.code,
                failure_class=failure.failure_class,
                message=failure.message,
                stage=failure.stage,
            )
            if failure
            else None,
            history=[StatusEventResponse(**e.to_dict()) for e in flow.history],
            limitations=list(flow.limitations),
            requested_by_user_id=flow.requested_by_user_id,
            requested_at=flow.requested_at,
            started_at=flow.started_at,
            completed_at=flow.completed_at,
            candidates=[CandidateSummary.of(flow, c) for c in detail.candidates],
            steps=[StepResponse.of(s) for s in detail.steps],
        )


class WorkflowSummary(ApiModel):
    id: uuid.UUID
    objective: str
    status: WorkflowStatus
    stage: Stage
    iteration: int
    candidates: int
    failure: FailureCode | None
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    completed_at: datetime | None

    @classmethod
    def of(cls, listing: WorkflowListing) -> WorkflowSummary:
        return cls(**{name: getattr(listing, name) for name in cls.model_fields})


class WorkflowPage(ApiModel):
    workflows: list[WorkflowSummary]
    next_cursor: str | None


class WorkflowApprovedResponse(ApiModel):
    workflow_id: uuid.UUID
    status: WorkflowStatus
    candidate: CandidateSummary
    architecture_id: uuid.UUID
    revision_number: int
    created_architecture: bool

    @classmethod
    def of(cls, approved: ApprovedCandidate) -> WorkflowApprovedResponse:
        flow, revision = approved.workflow, approved.workflow.approved
        if revision is None:  # an approved workflow always names its revision
            raise ValueError("approved workflow without a revision")
        return cls(
            workflow_id=flow.id,
            status=flow.status,
            candidate=CandidateSummary.of(flow, approved.candidate),
            architecture_id=revision.architecture_id,
            revision_number=revision.number,
            created_architecture=approved.created_architecture,
        )
