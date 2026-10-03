"""Architecture diffs over HTTP, typed field by field.

A diff names two exact states (a revision, or an agent run's candidate) and reports, deterministically,
what changed (every change in exactly one group), what the changes touch (requirements through traces
and validation verdicts, ADRs that may require review) and what the engines found in each state. An
explanation is a separate, appended run: every statement cites what it rests on or is labelled an
inference. No score, no winner. A secret's change is reported, never its values. What is not known is
``null`` or said in ``limitations`` — never 0. Prompts, retrieved text and the model's raw output are
never returned (they are not stored).
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from core.architecture_ir.diff import ChangeKind
from core.architecture_ir.errors import ElementType
from core.domain.architecture_diff.changes import Change, ChangeGroup, FieldDelta
from core.domain.architecture_diff.diff_service import DiffReport
from core.domain.architecture_diff.explanations import DiffExplanation, ExplanationRun, Statement
from core.domain.architecture_diff.impacts import DecisionImpact, EngineImpact, RequirementImpact
from core.domain.architecture_diff.references import (
    MAX_CONTEXT,
    MAX_SCOPE,
    ComparedState,
    DiffRequest,
    StateRef,
)
from core.domain.architecture_diff.repository import DiffListing
from core.domain.architecture_diff.values import (
    Basis,
    ChangeClass,
    ExplanationFailure,
    ExplanationStatus,
    FindingState,
    ImpactStatus,
    RequirementRelation,
    Sensitivity,
    StateKind,
    ValueType,
)

from .architecture_agent import EvidenceResponse, RejectionResponse, UsageResponse
from .common import ApiModel, RequestModel

# --- requests ------------------------------------------------------------------------------------


class StateModel(RequestModel):
    """A revision (``architectureId`` and ``revisionNumber``) or an agent run's candidate (``runId``)."""

    kind: StateKind
    architecture_id: uuid.UUID | None = None
    revision_number: int | None = Field(default=None, ge=1)
    run_id: uuid.UUID | None = None

    def to_domain(self) -> StateRef:
        return StateRef(self.kind, self.architecture_id, self.revision_number, self.run_id)


class DiffCreateRequest(RequestModel):
    base: StateModel
    target: StateModel
    requirement_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=MAX_SCOPE,
        description="Requirements to report on; each gets a relation, including no_relationship or "
        "undetermined. Omitted: the requirements the changes touch.",
    )
    capacity_analysis_id: uuid.UUID | None = Field(
        default=None,
        description="A stored capacity analysis of a compared architecture: its workload compares capacity. "
        "Omitted: capacity is not evaluated.",
    )
    cost_analysis_id: uuid.UUID | None = Field(
        default=None,
        description="A stored cost analysis of a compared architecture: its pricing snapshot, date and hours "
        "compare cost. Omitted: cost is not evaluated.",
    )
    context: str | None = Field(
        default=None,
        max_length=MAX_CONTEXT,
        description="Your own words about the change, for the explanation (cited as user input).",
    )
    explain: bool = Field(default=False, description="Also ask for an AI explanation now.")

    def to_domain(self) -> DiffRequest:
        scope = tuple(self.requirement_ids) if self.requirement_ids is not None else None
        return DiffRequest(
            self.base.to_domain(),
            self.target.to_domain(),
            scope,
            self.capacity_analysis_id,
            self.cost_analysis_id,
            self.context,
            self.explain,
        )


# --- responses -----------------------------------------------------------------------------------


class StateResponse(ApiModel):
    kind: StateKind
    architecture_id: uuid.UUID | None
    revision_number: int | None
    run_id: uuid.UUID | None
    content_hash: str
    label: str

    @classmethod
    def of(cls, state: ComparedState) -> StateResponse:
        ref = state.ref
        return cls(
            kind=ref.kind,
            architecture_id=ref.architecture_id,
            revision_number=ref.revision_number,
            run_id=ref.run_id,
            content_hash=state.content_hash,
            label=state.label,
        )


class FieldResponse(ApiModel):
    path: str
    before: Any = Field(description="Canonical value; null when absent (beforeType says) or a secret.")
    after: Any
    before_type: ValueType
    after_type: ValueType
    category: str
    classes: list[ChangeClass]
    sensitivity: Sensitivity
    unit: str | None

    @classmethod
    def of(cls, delta: FieldDelta) -> FieldResponse:
        return cls(**delta.to_dict())


class ChangeResponse(ApiModel):
    id: str
    element: ElementType
    element_id: str
    change: ChangeKind
    label: str | None
    kind: str | None
    classes: list[ChangeClass] = Field(description="What the change concerns, never what it does.")
    fields: list[FieldResponse]
    renamed: bool
    endpoints: list[str] | None

    @classmethod
    def of(cls, change: Change) -> ChangeResponse:
        return cls(**change.to_dict() | {"fields": [FieldResponse.of(f) for f in change.fields]})


class GroupResponse(ApiModel):
    id: str
    rule: str
    title: str
    reason: str = Field(description="Why these changes are together, in the rule's words.")
    change_ids: list[str]
    element_ids: list[str]

    @classmethod
    def of(cls, group: ChangeGroup) -> GroupResponse:
        return cls(**group.to_dict())


class RequirementImpactResponse(ApiModel):
    requirement_id: uuid.UUID
    reference: str
    version: int | None
    title: str
    statement: str
    relation: RequirementRelation
    element_ids: list[str]
    change_ids: list[str]
    base_verdict: str | None = Field(description="The validation engine's; null: not evaluated.")
    target_verdict: str | None

    @classmethod
    def of(cls, impact: RequirementImpact) -> RequirementImpactResponse:
        return cls(**impact.to_dict())


class DecisionImpactResponse(ApiModel):
    decision_id: uuid.UUID
    reference: str
    title: str
    status: str
    element_ids: list[str]
    change_ids: list[str]
    note: str

    @classmethod
    def of(cls, impact: DecisionImpact) -> DecisionImpactResponse:
        return cls(**impact.to_dict())


class FindingDeltaResponse(ApiModel):
    finding_id: str
    state: FindingState
    severity: str
    title: str
    elements: list[str]


class MeasureResponse(ApiModel):
    name: str
    before: str
    after: str
    unit: str


class EngineImpactResponse(ApiModel):
    engine: str
    status: ImpactStatus
    versions: dict[str, Any]
    findings: list[FindingDeltaResponse] = Field(description="Introduced and resolved, by stable id.")
    unchanged: int = Field(description="Findings present in both states (counted, not listed).")
    base_summary: dict[str, Any] = Field(description="The engine's own counts in the base; never a score.")
    target_summary: dict[str, Any]
    measures: list[MeasureResponse] = Field(description="Only values the engine produced in both states.")
    limitations: list[str]
    error: str | None

    @classmethod
    def of(cls, impact: EngineImpact) -> EngineImpactResponse:
        return cls(**impact.to_dict())


class GroundingResponse(ApiModel):
    basis: Basis
    ref: str


class StatementResponse(ApiModel):
    text: str
    groundings: list[GroundingResponse]
    inferred: bool = Field(description="An AI hypothesis to review: never presented as fact.")

    @classmethod
    def of(cls, statement: Statement) -> StatementResponse:
        return cls(**statement.to_dict())


class GroupExplanationResponse(ApiModel):
    group_id: str
    title: str
    explanation: StatementResponse
    consequences: list[StatementResponse]
    unknowns: list[str]


class RequirementExplanationResponse(ApiModel):
    reference: str
    explanation: StatementResponse


class ExplanationResponse(ApiModel):
    summary: StatementResponse
    groups: list[GroupExplanationResponse]
    tradeoffs: list[StatementResponse]
    requirements: list[RequirementExplanationResponse]
    risks: list[StatementResponse] = Field(description="Areas to review.")
    questions: list[StatementResponse] = Field(description="Review questions: they ask, never assume.")
    unknowns: list[str]

    @classmethod
    def of(cls, explanation: DiffExplanation) -> ExplanationResponse:
        return cls(**explanation.to_dict())


class ExplanationRunResponse(ApiModel):
    id: uuid.UUID
    diff_id: uuid.UUID
    status: ExplanationStatus
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    model: str | None
    prompt_version: str | None
    usage: UsageResponse
    explanation: ExplanationResponse | None
    evidence: list[EvidenceResponse] = Field(description="The passages it cites, by citation.")
    failure: ExplanationFailure | None
    rejections: list[RejectionResponse] = Field(description="Why the model's output could not be used.")
    limitations: list[str]

    @classmethod
    def of(cls, run: ExplanationRun) -> ExplanationRunResponse:
        usage = run.usage
        return cls(
            id=run.id,
            diff_id=run.diff_id,
            status=run.status,
            requested_by_user_id=run.requested_by_user_id,
            requested_at=run.requested_at,
            model=run.model,
            prompt_version=run.prompt_version,
            usage=UsageResponse(
                model_calls=usage.model_calls,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                model_latency_ms=usage.model_latency_ms,
                retrieval_calls=usage.retrieval_calls,
                engine_runs=usage.engine_runs,
            ),
            explanation=ExplanationResponse.of(run.explanation) if run.explanation else None,
            evidence=[EvidenceResponse(**e.to_dict()) for e in run.evidence],
            failure=run.failure,
            rejections=[RejectionResponse(**r.to_dict()) for r in run.rejections],
            limitations=list(run.limitations),
        )


class DiffRequestResponse(ApiModel):
    requirement_ids: list[uuid.UUID] | None
    capacity_analysis_id: uuid.UUID | None
    cost_analysis_id: uuid.UUID | None
    context: str | None
    explain: bool


class DiffResponse(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    base: StateResponse
    target: StateResponse
    request: DiffRequestResponse
    identical: bool
    counts: dict[str, int] = Field(description="Changes by kind and by class: counts only, never a score.")
    changes: list[ChangeResponse]
    groups: list[GroupResponse] = Field(description="Every change is in exactly one group.")
    requirements: list[RequirementImpactResponse]
    decisions: list[DecisionImpactResponse]
    engines: list[EngineImpactResponse]
    versions: dict[str, Any]
    warnings: list[str]
    unknowns: list[str]
    requested_by_user_id: uuid.UUID
    compared_at: datetime
    explanations: list[ExplanationRunResponse] = Field(description="Oldest first: the latest is the last.")

    @classmethod
    def of(cls, report: DiffReport) -> DiffResponse:
        diff, asked = report.diff, report.diff.request
        return cls(
            id=diff.id,
            project_id=diff.project_id,
            base=StateResponse.of(diff.base),
            target=StateResponse.of(diff.target),
            request=DiffRequestResponse(
                requirement_ids=list(asked.requirement_ids) if asked.requirement_ids is not None else None,
                capacity_analysis_id=asked.capacity_analysis_id,
                cost_analysis_id=asked.cost_analysis_id,
                context=asked.context,
                explain=asked.explain,
            ),
            identical=diff.identical,
            counts=diff.semantic.counts(),
            changes=[ChangeResponse.of(c) for c in diff.semantic.changes],
            groups=[GroupResponse.of(g) for g in diff.semantic.groups],
            requirements=[RequirementImpactResponse.of(r) for r in diff.requirements],
            decisions=[DecisionImpactResponse.of(d) for d in diff.decisions],
            engines=[EngineImpactResponse.of(e) for e in diff.engines],
            versions=dict(diff.semantic.versions),
            warnings=list(diff.warnings),
            unknowns=list(diff.unknowns),
            requested_by_user_id=diff.requested_by_user_id,
            compared_at=diff.created_at,
            explanations=[ExplanationRunResponse.of(r) for r in report.explanations],
        )


class DiffSummary(ApiModel):
    id: uuid.UUID
    base: StateResponse
    target: StateResponse
    change_count: int
    counts: dict[str, int]
    explanations: int
    requested_by_user_id: uuid.UUID
    compared_at: datetime

    @classmethod
    def of(cls, listing: DiffListing) -> DiffSummary:
        return cls(
            id=listing.id,
            base=StateResponse.of(listing.base),
            target=StateResponse.of(listing.target),
            change_count=listing.change_count,
            counts=dict(listing.counts),
            explanations=listing.explanations,
            requested_by_user_id=listing.requested_by_user_id,
            compared_at=listing.compared_at,
        )


class DiffPage(ApiModel):
    diffs: list[DiffSummary]
    next_cursor: str | None
