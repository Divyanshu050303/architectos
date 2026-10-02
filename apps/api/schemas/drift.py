"""Drift detection over HTTP, typed field by field. A drift analysis compares one exact architecture
revision with one stored discovery run: what the sources declare, never the running system. Findings
are classified (confirmed, potential, not comparable, unknown) — there is no drift score — and what
was not inspected is unknown, never absent. Reviewing a drift item never changes the architecture."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, StrictInt

from core.domain.drift.analyses import MAX_EXCLUDED, MAX_LABEL, DriftAnalysis, DriftRequest
from core.domain.drift.findings import DriftFinding
from core.domain.drift.identity import MAX_NOTE, IdentityMapping
from core.domain.drift.items import DriftItem, Link
from core.domain.drift.values import (
    AnalysisStatus,
    Classification,
    Compatibility,
    ElementType,
    FindingType,
    LinkKind,
    MatchMethod,
    ReviewAction,
    ReviewStatus,
)
from core.domain.evolution.values import EvidenceSource, EvidenceState

from .common import ApiModel, RequestModel

type Identifier = Annotated[str, Field(min_length=1, max_length=128)]
DECLARED = (
    "Drift compares what the baseline revision states with what the discovered artifacts declare — a "
    "desired state, not the running system. What was not inspected is unknown, never absent; nothing "
    "here changes the architecture."
)

# --- requests --------------------------------------------------------------------------------------


class DriftAnalysisRequest(RequestModel):
    architecture_id: uuid.UUID
    baseline_revision: StrictInt = Field(ge=1, description="The exact revision compared.")
    discovery_run_id: uuid.UUID = Field(description="A completed discovery run of this project.")
    policy: Annotated[str, Field(min_length=1, max_length=64)] = Field(
        default="default", description="The comparison policy; only 'default' exists."
    )
    exclude: list[Identifier] = Field(
        default_factory=list,
        max_length=MAX_EXCLUDED,
        description="Baseline element ids or discovery keys left out of the comparison.",
    )
    label: Annotated[str | None, Field(max_length=MAX_LABEL)] = None

    def to_domain(self) -> DriftRequest:
        return DriftRequest(
            self.architecture_id, self.baseline_revision, self.discovery_run_id, self.policy,
            tuple(self.exclude), self.label,
        )  # fmt: skip


class LinkInput(RequestModel):
    kind: LinkKind
    target: Annotated[str, Field(min_length=1, max_length=512)] = Field(
        description="The decision, migration plan or evolution analysis id; for a revision, its number."
    )
    architecture_id: uuid.UUID | None = Field(default=None, description="A revision link only.")

    def to_domain(self) -> Link:
        return Link(self.kind, self.target, self.architecture_id)


class ReviewRequest(RequestModel):
    action: ReviewAction = Field(
        description="acknowledge, investigate, accept, dismiss (note), resolve (evidence), reopen (note), "
        "note (note) or link (link). 'detected' is recorded by analyses only."
    )
    note: Annotated[str | None, Field(max_length=MAX_NOTE)] = None
    link: LinkInput | None = None
    evidence_analysis_id: uuid.UUID | None = Field(
        default=None,
        description="Resolving: a later drift analysis of the same architecture that inspected the item's "
        "sources and no longer detects it.",
    )


class IdentityMappingRequest(RequestModel):
    baseline_id: Identifier = Field(description="A node of the architecture's current revision.")
    discovered_key: Identifier | None = Field(
        description="The discovered entity it is; null retracts the mapping."
    )
    note: Annotated[str | None, Field(max_length=MAX_NOTE)] = None


# --- responses -------------------------------------------------------------------------------------


class ErrorModel(ApiModel):
    code: str
    message: str


class BaselineModel(ApiModel):
    architecture_id: uuid.UUID
    revision_number: int
    content_hash: str
    schema_version: int


class ObservedModel(ApiModel):
    discovery_run_id: uuid.UUID
    result_fingerprint: str
    sources_fingerprint: str
    result_version: int
    extractors: dict[str, int]


class CompatibilityModel(ApiModel):
    dimension: str
    outcome: Compatibility
    message: str


class CoverageModel(ApiModel):
    source_types: list[str]
    inspected: list[str] = Field(description="Artifacts read completely.")
    partial: list[str]
    unread: list[str]
    unsupported_constructs: int
    unresolved_entities: int
    unresolved_relationships: int
    errors: int
    complete: bool


class ImpactModel(ApiModel):
    engine: EvidenceSource
    basis: str = Field(description="Why that engine's model reads what the finding concerns.")
    state: EvidenceState = Field(
        description="current: of the baseline's exact content; stale: not to be relied on; missing."
    )
    analysis_id: str | None
    revision_number: int | None
    items: list[str]


class DriftFindingModel(ApiModel):
    id: str = Field(description="Stable: the same difference has the same id in every analysis.")
    item_key: str
    type: FindingType
    classification: Classification
    element: ElementType
    subject: str
    explanation: str
    rule: str
    path: str | None
    baseline_id: str | None
    discovered_key: str | None
    match: MatchMethod | None
    baseline_value: Any = Field(description="Null when absent or redacted.")
    discovered_value: Any
    redacted: bool = Field(description="A secret: that it changed is stated, its values never.")
    evidence: list[str] = Field(description="Discovery finding ids.")
    locations: list[str]
    baseline_reference: str | None
    limitations: list[str]
    impact: list[ImpactModel] = Field(description="Context from stored analyses — not part of the finding.")
    references: list[str]


class DriftSummaryModel(ApiModel):
    compatibility: Compatibility
    findings: int
    types: dict[str, int]
    classifications: dict[str, int]
    no_difference_within_coverage: bool


class DriftAnalysisSummary(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    baseline_revision: int
    discovery_run_id: uuid.UUID
    policy: str
    exclude: list[str]
    label: str | None
    status: AnalysisStatus
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    completed_at: datetime | None
    summary: DriftSummaryModel | None = Field(description="Counts only — no score.")
    fingerprint: str | None
    error: ErrorModel | None

    @classmethod
    def of(cls, analysis: DriftAnalysis) -> DriftAnalysisSummary:
        request, result = analysis.request, analysis.result
        return cls(
            id=analysis.id,
            project_id=analysis.project_id,
            architecture_id=request.architecture_id,
            baseline_revision=request.baseline_revision,
            discovery_run_id=request.discovery_run_id,
            policy=request.policy,
            exclude=list(request.exclude),
            label=request.label,
            status=analysis.status,
            requested_by_user_id=analysis.requested_by_user_id,
            requested_at=analysis.requested_at,
            completed_at=analysis.completed_at,
            summary=DriftSummaryModel.model_validate(result.summary()) if result else None,
            fingerprint=result.fingerprint if result else None,
            error=ErrorModel.model_validate(analysis.error.to_dict()) if analysis.error else None,
        )


class DriftAnalysisResponse(DriftAnalysisSummary):
    note: str = Field(default=DECLARED)
    baseline: BaselineModel | None
    observed: ObservedModel | None
    compatibility: list[CompatibilityModel]
    coverage: CoverageModel | None
    warnings: list[str]
    versions: dict[str, int]
    findings: int = Field(description="How many; the findings themselves: GET .../findings.")

    @classmethod
    def of_analysis(cls, analysis: DriftAnalysis) -> DriftAnalysisResponse:
        result = analysis.result
        content = result.to_dict() if result else {}
        return cls.model_validate(
            DriftAnalysisSummary.of(analysis).model_dump()
            | {
                "baseline": content.get("baseline"),
                "observed": content.get("observed"),
                "compatibility": content.get("compatibility", []),
                "coverage": content.get("coverage"),
                "warnings": content.get("warnings", []),
                "versions": content.get("versions", {}),
                "findings": len(result.findings) if result else 0,
            }
        )


class DriftAnalysisPage(ApiModel):
    analyses: list[DriftAnalysisSummary]
    next_cursor: str | None


class DriftFindingsResponse(ApiModel):
    drift_analysis_id: uuid.UUID
    findings: list[DriftFindingModel]

    @classmethod
    def of(cls, analysis_id: uuid.UUID, findings: tuple[DriftFinding, ...]) -> DriftFindingsResponse:
        return cls(
            drift_analysis_id=analysis_id,
            findings=[DriftFindingModel.model_validate(f.to_dict()) for f in findings],
        )


class LinkModel(ApiModel):
    kind: LinkKind
    target: str
    architecture_id: uuid.UUID | None


class ReviewEventModel(ApiModel):
    action: ReviewAction
    previous: ReviewStatus
    status: ReviewStatus
    at: datetime
    user_id: uuid.UUID | None = Field(description="Null: recorded by an analysis.")
    note: str | None
    link: LinkModel | None
    analysis_id: uuid.UUID | None


class DriftItemResponse(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    key: str
    element: ElementType
    subject: str
    path: str | None
    type: FindingType = Field(description="The latest finding's.")
    classification: Classification = Field(description="The latest finding's.")
    first_analysis_id: uuid.UUID
    last_analysis_id: uuid.UUID = Field(description="The latest analysis that detected it.")
    status: ReviewStatus = Field(description="A person's review — separate from what was found.")
    history: list[ReviewEventModel]
    links: list[LinkModel]
    artifacts: list[str] = Field(description="Where its findings were read: what a resolution must inspect.")

    @classmethod
    def of(cls, item: DriftItem) -> DriftItemResponse:
        return cls.model_validate(item.to_dict())


class DriftItemPage(ApiModel):
    items: list[DriftItemResponse]
    next_cursor: str | None


class IdentityMappingResponse(ApiModel):
    architecture_id: uuid.UUID
    baseline_id: str
    discovered_key: str | None = Field(description="Null: retracted.")
    confirmed_by_user_id: uuid.UUID
    confirmed_at: datetime
    note: str | None

    @classmethod
    def of(cls, mapping: IdentityMapping) -> IdentityMappingResponse:
        return cls.model_validate(mapping.to_dict())


class IdentityMappingsResponse(ApiModel):
    architecture_id: uuid.UUID
    mappings: list[IdentityMappingResponse] = Field(
        description="Every confirmation, oldest first; the latest per baseline node applies."
    )
