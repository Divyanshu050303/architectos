"""Migration plans over HTTP, typed field by field. A plan is a proposal for engineering review between
an exact source revision and an exact target: nothing here executes a step, switches traffic or
moves data, and nothing is approved automatically. An unknown value is null or ``unknown``; no score,
probability, duration or volume is ever stated."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, StrictBool, StrictInt

from core.domain.evolution.values import EvidenceSource
from core.domain.migrations.entities import (
    Assumption,
    DataRequirement,
    MigrationConstraints,
    MigrationPlanVersion,
    MigrationRequest,
    TargetSpec,
)
from core.domain.migrations.evidence import CoverageState, Side
from core.domain.migrations.migration_service import PlanView
from core.domain.migrations.steps import CompatibilityAspect
from core.domain.migrations.values import (
    CheckpointBasis,
    CheckpointStatus,
    CompatibilityStatus,
    DowntimeStatus,
    FindingType,
    PlanStatus,
    Reversibility,
    RiskCategory,
    RiskStatus,
    StepType,
    TargetKind,
    TraceKind,
)
from core.domain.migrations.versioning import StaleReason

from .common import ApiModel, RequestModel
from .evolution import EvidenceRefModel

type Text = Annotated[str, Field(min_length=1, max_length=2000)]
type ElementId = Annotated[str, Field(min_length=1, max_length=128)]
type Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

# --- requests --------------------------------------------------------------------------------------


class TargetInput(RequestModel):
    revision: StrictInt | None = Field(
        default=None, ge=1, description="A later revision of the architecture."
    )
    analysis_id: uuid.UUID | None = Field(
        default=None, description="An evolution analysis of the source revision."
    )
    candidate_id: Annotated[str | None, Field(max_length=64)] = Field(
        default=None, description="A candidate of that analysis."
    )


class PlanConstraintsInput(RequestModel):
    downtime_allowed: StrictBool | None = Field(default=None, description="null: not stated (never assumed).")
    maintenance_window: Annotated[str | None, Field(max_length=200)] = None
    statements: list[Text] = Field(default_factory=list, max_length=50)


class DataRequirementInput(RequestModel):
    element_id: ElementId
    statement: Text


class PlanAssumptionInput(RequestModel):
    key: Annotated[str, Field(min_length=1, max_length=64)]
    statement: Text


class MigrationPlanRequest(RequestModel):
    architecture_id: uuid.UUID
    source_revision: StrictInt = Field(ge=1)
    target: TargetInput = Field(
        description="Either a later revision, or an evolution analysis and candidate."
    )
    goals: list[Text] = Field(default_factory=list, max_length=20)
    constraints: PlanConstraintsInput | None = None
    data_requirements: list[DataRequirementInput] = Field(default_factory=list, max_length=50)
    requirement_ids: Annotated[list[uuid.UUID] | None, Field(max_length=200)] = None
    strategy: Annotated[str | None, Field(max_length=64)] = Field(
        default=None, description="A preferred strategy; followed only when its prerequisites are declared."
    )
    assumptions: list[PlanAssumptionInput] = Field(default_factory=list, max_length=50)
    title: Annotated[str | None, Field(max_length=200)] = None

    def to_domain(self) -> MigrationRequest:
        """Validated by the domain's request rules."""
        constraints = self.constraints or PlanConstraintsInput()
        return MigrationRequest(
            architecture_id=self.architecture_id,
            source_revision=self.source_revision,
            target=TargetSpec(self.target.revision, self.target.analysis_id, self.target.candidate_id),
            goals=tuple(self.goals),
            constraints=MigrationConstraints(
                constraints.downtime_allowed, constraints.maintenance_window, tuple(constraints.statements)
            ),
            data_requirements=tuple(
                DataRequirement(d.element_id, d.statement) for d in self.data_requirements
            ),
            requirement_ids=tuple(self.requirement_ids) if self.requirement_ids is not None else None,
            strategy=self.strategy,
            assumptions=tuple(Assumption(a.key, a.statement) for a in self.assumptions),
            title=self.title,
        )


class RegeneratePlanRequest(RequestModel):
    request: MigrationPlanRequest | None = Field(
        default=None, description="A revised request; omitted: the latest version's request, regenerated."
    )
    replace_reviewed: StrictBool = Field(
        default=False, description="Required to supersede a version under review or approved."
    )


class ApprovePlanRequest(RequestModel):
    fingerprint: Fingerprint = Field(description="The fingerprint of the exact version reviewed.")
    comment: Annotated[str | None, Field(max_length=2000)] = None


class RejectPlanRequest(RequestModel):
    fingerprint: Fingerprint = Field(description="The fingerprint of the exact version reviewed.")
    comment: Text = Field(description="Why: kept with the version.")


# --- parts -----------------------------------------------------------------------------------------


class TraceModel(ApiModel):
    kind: TraceKind
    reference: str
    detail: str | None


class StepModel(ApiModel):
    id: str
    key: str
    type: StepType
    title: str
    description: str | None
    element_ids: list[str]
    preconditions: list[str]
    inputs: list[str]
    outcome: str
    completion: list[str]
    depends_on: list[str] = Field(description="Step ids.")
    parallelizable: bool
    downtime: DowntimeStatus
    downtime_note: str | None
    traffic: str | None
    availability: list[str]
    data_impact: str | None
    reversibility: Reversibility
    manual_verification: bool
    assumptions: list[str]
    traces: list[TraceModel]


class StageModel(ApiModel):
    number: int
    step_ids: list[str]
    parallel: bool = Field(description="Only when every step of the stage is explicitly parallelizable.")


class RiskModel(ApiModel):
    id: str
    key: str
    category: RiskCategory
    status: RiskStatus
    description: str
    impact: str
    element_ids: list[str]
    step_ids: list[str]
    preconditions: list[str]
    mitigation: str | None
    traces: list[TraceModel]


class CheckpointModel(ApiModel):
    id: str
    key: str
    subject: str
    expected: str
    status: CheckpointStatus = Field(description="A planning-time status: never proof of runtime success.")
    basis: CheckpointBasis
    blocking: bool
    actual: str | None
    step_ids: list[str]
    evidence: list[EvidenceRefModel]
    traces: list[TraceModel]


class RollbackModel(ApiModel):
    id: str
    step_id: str
    reversibility: Reversibility
    triggers: list[str]
    action: str | None = Field(description="A proposal in words, never a command.")
    retained: list[str]
    preconditions: list[str]
    consistency: str | None
    verification: list[str]
    limitations: list[str]
    traces: list[TraceModel]


class DataMigrationModel(ApiModel):
    id: str
    key: str
    source_element_id: str | None
    destination_element_id: str | None
    scope: str | None
    method: str | None
    backfill: str | None
    replication: str | None
    cutover: list[str]
    duration: str = Field(description="Always unevaluable: volume and throughput are not modeled.")
    step_ids: list[str]
    verification: list[str]
    retention: list[str]
    rollback: str | None
    data_loss: str | None
    missing: list[str]
    traces: list[TraceModel]


class CompatibilityModel(ApiModel):
    id: str
    key: str
    aspect: CompatibilityAspect
    status: CompatibilityStatus = Field(description="verified only with machine-checkable evidence.")
    question: str
    element_ids: list[str]
    note: str | None
    traces: list[TraceModel]


class PlanFindingModel(ApiModel):
    id: str
    type: FindingType
    key: str
    message: str
    element_ids: list[str]
    step_ids: list[str]
    missing: list[str]
    traces: list[TraceModel]


class StrategyOptionModel(ApiModel):
    pattern: str
    name: str
    applies: bool
    supported: bool
    subjects: list[str]
    missing: list[str]
    tradeoffs: list[str]
    downtime: DowntimeStatus
    reversibility: Reversibility


class CoverageModel(ApiModel):
    source: EvidenceSource
    side: Side
    state: CoverageState
    analysis_ids: list[str]
    note: str | None


class SourceModel(ApiModel):
    architecture_id: uuid.UUID
    revision_number: int
    content_hash: str


class TargetModel(ApiModel):
    kind: TargetKind
    architecture_id: uuid.UUID
    revision_number: int = Field(description="A candidate's: its baseline (the source revision).")
    content_hash: str
    analysis_id: uuid.UUID | None
    candidate_id: str | None


class ReviewModel(ApiModel):
    from_status: PlanStatus
    to_status: PlanStatus
    user_id: uuid.UUID
    at: datetime
    comment: str | None
    fingerprint: str | None = Field(description="For an approval or a rejection: the exact content reviewed.")


class FreshnessModel(ApiModel):
    stale: bool
    reasons: list[StaleReason]


# --- plans -----------------------------------------------------------------------------------------


class MigrationPlanSummary(ApiModel):
    id: uuid.UUID = Field(description="This version's id.")
    plan_id: uuid.UUID
    version: int
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    title: str
    status: PlanStatus = Field(description="A review status — never an execution status.")
    source: SourceModel
    target: TargetModel
    strategy: str | None
    fingerprint: str
    summary: dict[str, Any] = Field(description="Counts only; no score.")
    reviews: list[ReviewModel]
    created_by_user_id: uuid.UUID
    created_at: datetime

    @classmethod
    def fields_for(cls, version: MigrationPlanVersion) -> dict[str, Any]:
        proposal = version.proposal
        return {
            "id": version.id,
            "plan_id": version.plan_id,
            "version": version.version,
            "project_id": version.project_id,
            "architecture_id": proposal.source.architecture_id,
            "title": version.title,
            "status": version.status,
            "source": proposal.source.to_dict(),
            "target": proposal.target.to_dict(),
            "strategy": proposal.strategy,
            "fingerprint": proposal.fingerprint,
            "summary": proposal.summary(),
            "reviews": [r.to_dict() for r in version.reviews],
            "created_by_user_id": version.created_by_user_id,
            "created_at": version.created_at,
        }

    @classmethod
    def of(cls, version: MigrationPlanVersion) -> MigrationPlanSummary:
        return cls.model_validate(cls.fields_for(version))


class MigrationPlanResponse(MigrationPlanSummary):
    freshness: FreshnessModel = Field(
        description="Computed when read: stale versions are shown, never approved."
    )
    request: dict[str, Any] = Field(description="The request this version answered, as stated.")
    steps: list[StepModel]
    sequence: list[StageModel] = Field(description="Empty when the dependencies are invalid or cyclic.")
    risks: list[RiskModel]
    assumptions: list[str]
    checkpoints: list[CheckpointModel]
    rollbacks: list[RollbackModel]
    data_migrations: list[DataMigrationModel]
    compatibility: list[CompatibilityModel]
    findings: list[PlanFindingModel]
    evidence: list[EvidenceRefModel]
    coverage: list[CoverageModel]
    alternatives: list[StrategyOptionModel]
    models: dict[str, int] = Field(description="The rule and model versions used.")
    diff_summary: str | None

    @classmethod
    def of_view(cls, view: PlanView) -> MigrationPlanResponse:
        content = view.version.proposal.to_dict()
        parts = {
            name: content[name]
            for name in (
                "steps", "sequence", "risks", "assumptions", "checkpoints", "rollbacks", "data_migrations",
                "compatibility", "findings", "evidence", "coverage", "alternatives", "models", "diff_summary",
            )
        }  # fmt: skip
        return cls.model_validate(
            cls.fields_for(view.version)
            | parts
            | {"freshness": view.freshness.to_dict(), "request": view.version.request.to_dict()}
        )


class MigrationPlanPage(ApiModel):
    plans: list[MigrationPlanSummary] = Field(description="The latest version of each plan, newest first.")
    next_cursor: str | None


class MigrationPlanHistory(ApiModel):
    versions: list[MigrationPlanSummary] = Field(description="Every version, by version number.")


class RegeneratedPlanResponse(ApiModel):
    created: bool = Field(description="False: the result was identical to the latest version.")
    plan: MigrationPlanResponse


class PlanStepsResponse(ApiModel):
    steps: list[StepModel]
    sequence: list[StageModel]


class PlanRisksResponse(ApiModel):
    risks: list[RiskModel]
    assumptions: list[str]


class PlanCheckpointsResponse(ApiModel):
    checkpoints: list[CheckpointModel]


class PlanRollbacksResponse(ApiModel):
    rollbacks: list[RollbackModel]
