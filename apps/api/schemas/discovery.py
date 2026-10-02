"""Discovery over HTTP, typed field by field. A discovery reads supplied artifacts — never executing,
evaluating or fetching anything — and proposes an architecture for a person to review: findings are
evidence (each with how it is known), the proposal is not an architecture until a person accepts it,
and what the sources do not establish stays unknown. Artifact content is never returned."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, StrictInt

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.discovery.comparison import BaselineComparison, Change, Comparability, ResultComparison
from core.domain.discovery.discovery_service import AcceptedProposal
from core.domain.discovery.findings import Finding
from core.domain.discovery.results import Proposal
from core.domain.discovery.runs import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS,
    ArtifactInput,
    Baseline,
    DiscoveryRequest,
    DiscoveryRun,
    ReviewDecision,
    RunListing,
    SubjectType,
)
from core.domain.discovery.values import (
    ArtifactStatus,
    Decision,
    ElementKind,
    ElementStatus,
    EntityRole,
    FindingType,
    MappingStatus,
    RelationshipStatus,
    RunStatus,
    Severity,
    SourceType,
    Verification,
)

from .common import ApiModel, RequestModel

type Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
DECLARED = (
    "Discovery reads what artifacts declare — a desired state — never the running system: a declared "
    "replica count, endpoint or dependency is not proof that it runs, is reachable or receives traffic."
)

# --- requests --------------------------------------------------------------------------------------


class ArtifactInputModel(RequestModel):
    path: Annotated[str, Field(min_length=1, max_length=256)] = Field(
        description="A relative name (no absolute path, no '..', no backslash), e.g. k8s/shop.yaml."
    )
    content: Annotated[str, Field(max_length=MAX_ARTIFACT_BYTES)] = Field(
        description="The artifact's text. Read, never executed, rendered or stored."
    )


class BaselineInput(RequestModel):
    architecture_id: uuid.UUID
    revision: StrictInt = Field(ge=1)


class DiscoveryRunRequest(RequestModel):
    artifacts: list[ArtifactInputModel] = Field(min_length=1, max_length=MAX_ARTIFACTS)
    source_type: SourceType | None = Field(
        default=None, description="Applies to every artifact; null: detected per artifact."
    )
    baseline: BaselineInput | None = Field(
        default=None, description="An architecture revision of this project to compare with later."
    )
    label: Annotated[str | None, Field(max_length=200)] = None

    def to_domain(self) -> DiscoveryRequest:
        baseline = Baseline(self.baseline.architecture_id, self.baseline.revision) if self.baseline else None
        artifacts = tuple(ArtifactInput(a.path, a.content) for a in self.artifacts)
        return DiscoveryRequest(artifacts, self.source_type, baseline, self.label)


class DecisionRequest(RequestModel):
    subject_type: SubjectType
    subject: Annotated[str, Field(min_length=1, max_length=256)] = Field(
        description="A candidate entity's key, or a candidate relationship's id."
    )
    decision: Decision = Field(description="accepted, rejected or ignored.")
    component_id: Annotated[str | None, Field(max_length=128)] = Field(
        default=None, description="Accepting an entity: its catalog component (an ambiguous one's candidate)."
    )
    node_kind: NodeKind | None = Field(
        default=None, description="Accepting an entity whose kind the source does not establish."
    )
    connection_kind: ConnectionKind | None = Field(
        default=None, description="Accepting a relationship whose kind the source does not establish."
    )
    comment: Annotated[str | None, Field(max_length=2000)] = None

    def to_domain(self, user_id: uuid.UUID, at: datetime) -> ReviewDecision:
        return ReviewDecision(
            self.subject_type, self.subject, self.decision, user_id, at, self.component_id, self.comment,
            self.node_kind, self.connection_kind,
        )  # fmt: skip


class AcceptRequest(RequestModel):
    proposal_content_hash: Fingerprint = Field(
        description="The content hash of the proposal reviewed (GET .../proposal): another one is refused."
    )
    architecture_id: uuid.UUID | None = Field(
        default=None, description="A new revision of this architecture; null: a new architecture."
    )
    base_version: StrictInt | None = Field(
        default=None, ge=1, description="With architectureId: the current revision it is based on."
    )
    name: Annotated[str | None, Field(min_length=1, max_length=100)] = Field(
        default=None, description="A new architecture's name."
    )


# --- responses -------------------------------------------------------------------------------------


class LocationModel(ApiModel):
    artifact: str
    document: int | None
    pointer: str | None
    line: int | None


class ArtifactModel(ApiModel):
    path: str
    content_hash: str
    size_bytes: int
    status: ArtifactStatus
    source_type: SourceType | None
    format_version: str | None
    documents: int
    extractor: str | None


class DiagnosticModel(ApiModel):
    id: str
    code: str
    severity: Severity
    message: str
    location: LocationModel | None


class FindingModel(ApiModel):
    id: str
    type: FindingType
    entity: str
    location: LocationModel
    verification: Verification
    extractor: str
    property: str | None
    source_property: str | None
    value: Any = Field(description="As written; null when redacted or absent.")
    redacted: bool = Field(description="A secret: its presence is recorded, never its value.")
    target: str | None
    warnings: list[str]


class MappingModel(ApiModel):
    status: MappingStatus
    rule: str
    component_id: str | None
    candidates: list[str]
    reason: str | None


class PropertyMappingModel(ApiModel):
    source_property: str
    property: str
    rule: str
    verification: Verification
    finding_id: str
    value: Any
    valid: bool
    problem: str | None
    source_value: Any
    transformation: str | None


class EntityModel(ApiModel):
    id: str
    key: str
    name: str
    source_type: SourceType
    resource_type: str
    location: LocationModel
    mapping: MappingModel
    kind: NodeKind | None = Field(description="null: the source does not establish it.")
    role: EntityRole
    kind_rule: str | None
    namespace: str | None
    technology: str | None
    technology_version: str | None
    configuration: list[PropertyMappingModel]
    finding_ids: list[str]


class RelationshipModel(ApiModel):
    id: str
    source: str
    reference: str
    location: LocationModel
    status: RelationshipStatus
    target: str | None
    kind: ConnectionKind | None = Field(description="null: the source does not establish it.")
    finding_ids: list[str]
    reason: str | None
    rule: str | None


class ValidationIssueModel(ApiModel):
    code: str
    severity: Severity
    message: str
    element_id: str | None
    rule: str | None


class ElementModel(ApiModel):
    kind: ElementKind
    subject: str
    status: ElementStatus
    element_id: str | None
    origin: Verification | None
    reason: str | None
    via: str | None
    finding_ids: list[str]


class DecisionModel(ApiModel):
    subject_type: SubjectType
    subject: str
    decision: Decision
    user_id: uuid.UUID
    at: datetime
    component_id: str | None
    comment: str | None
    node_kind: NodeKind | None
    connection_kind: ConnectionKind | None


class AcceptanceModel(ApiModel):
    architecture_id: uuid.UUID
    revision_number: int
    content_hash: str
    user_id: uuid.UUID
    at: datetime


class RunErrorModel(ApiModel):
    code: str
    message: str


class BaselineModel(ApiModel):
    architecture_id: uuid.UUID
    revision: int

    @classmethod
    def of(cls, baseline: Baseline | None) -> BaselineModel | None:
        if baseline is None:
            return None
        return cls(architecture_id=baseline.architecture_id, revision=baseline.revision_number)


class DiscoveryRunSummary(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: RunStatus
    source_type: SourceType | None
    label: str | None
    baseline: BaselineModel | None
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    completed_at: datetime | None
    summary: dict[str, Any] | None = Field(description="Counts only — no score.")
    fingerprint: str | None = Field(description="The result's: equal for identical inputs and versions.")
    sources_fingerprint: str | None = Field(description="The inputs': each artifact's path and content hash.")
    error: RunErrorModel | None
    decisions: int
    acceptances: int

    @classmethod
    def of(cls, run: RunListing) -> DiscoveryRunSummary:
        error = RunErrorModel(code=run.error.code, message=run.error.message) if run.error else None
        return cls(
            id=run.id, project_id=run.project_id, status=run.status, source_type=run.source_type,
            label=run.label, baseline=BaselineModel.of(run.baseline),
            requested_by_user_id=run.requested_by_user_id, requested_at=run.requested_at,
            completed_at=run.completed_at, summary=run.summary, fingerprint=run.fingerprint,
            sources_fingerprint=run.sources_fingerprint, error=error, decisions=run.decisions,
            acceptances=run.acceptances,
        )  # fmt: skip


class DiscoveryRunResponse(DiscoveryRunSummary):
    note: str = Field(default=DECLARED)
    artifacts: list[ArtifactModel]
    entities: list[EntityModel]
    relationships: list[RelationshipModel]
    diagnostics: list[DiagnosticModel]
    elements: list[ElementModel] = Field(description="What became of each candidate in the proposal.")
    validation: list[ValidationIssueModel]
    proposed: dict[str, Any] | None = Field(
        description="The proposal without review decisions, in Architecture IR form: not an architecture."
    )
    unresolved: list[str] = Field(description="Entity keys and relationship ids a person must resolve.")
    extractors: dict[str, int] = Field(description="The version of every extractor and rule used.")
    review: list[DecisionModel]
    accepted: list[AcceptanceModel]

    @classmethod
    def of_run(cls, run: DiscoveryRun) -> DiscoveryRunResponse:
        result = run.result
        content = result.to_dict() if result else {}
        listing = RunListing(
            run.id, run.project_id, run.status, run.requested_by_user_id, run.requested_at, run.source_type,
            run.baseline, run.label, run.completed_at, result.summary() if result else None,
            result.fingerprint if result else None, result.sources_fingerprint if result else None,
            run.error, len(run.decisions), len(run.acceptances),
        )  # fmt: skip
        return cls.model_validate(
            DiscoveryRunSummary.of(listing).model_dump()
            | {
                "artifacts": content.get("artifacts", []),
                "entities": content.get("entities", []),
                "relationships": content.get("relationships", []),
                "diagnostics": content.get("diagnostics", []),
                "elements": content.get("elements", []),
                "validation": content.get("validation", []),
                "proposed": content.get("proposed"),
                "unresolved": content.get("unresolved", []),
                "extractors": content.get("extractors", {}),
                "review": [d.to_dict() for d in run.decisions],
                "accepted": [a.to_dict() for a in run.acceptances],
            }
        )


class DiscoveryRunPage(ApiModel):
    runs: list[DiscoveryRunSummary]
    next_cursor: str | None


class FindingsResponse(ApiModel):
    run_id: uuid.UUID
    findings: list[FindingModel]

    @classmethod
    def of(cls, run_id: uuid.UUID, findings: tuple[Finding, ...]) -> FindingsResponse:
        return cls(run_id=run_id, findings=[FindingModel.model_validate(f.to_dict()) for f in findings])


class ProposalResponse(ApiModel):
    run_id: uuid.UUID
    note: str = Field(default=DECLARED)
    architecture: dict[str, Any] | None = Field(description="Architecture IR form; null: nothing to propose.")
    content_hash: str | None = Field(description="What POST .../accept must name.")
    elements: list[ElementModel]
    validation: list[ValidationIssueModel]
    acceptance_problem: str | None = Field(
        description="Why it cannot be accepted: nothing_to_accept, structurally_invalid; null: it can."
    )

    @classmethod
    def of(cls, run_id: uuid.UUID, proposal: Proposal) -> ProposalResponse:
        architecture = proposal.architecture
        return cls(
            run_id=run_id,
            architecture=to_dict(architecture) if architecture else None,
            content_hash=content_hash(architecture) if architecture else None,
            elements=[ElementModel.model_validate(e.to_dict()) for e in proposal.elements],
            validation=[ValidationIssueModel.model_validate(v.to_dict()) for v in proposal.validation],
            acceptance_problem=proposal.acceptance_problem,
        )


class ReviewResponse(ApiModel):
    run_id: uuid.UUID
    review: list[DecisionModel] = Field(
        description="Every decision, in order; the latest per subject applies."
    )

    @classmethod
    def of(cls, run: DiscoveryRun) -> ReviewResponse:
        return cls(run_id=run.id, review=[DecisionModel.model_validate(d.to_dict()) for d in run.decisions])


class AcceptedResponse(ApiModel):
    run_id: uuid.UUID
    architecture_id: uuid.UUID
    revision: int
    content_hash: str
    created_architecture: bool
    created_revision: bool = Field(description="false: the content already was the current revision's.")

    @classmethod
    def of(cls, accepted: AcceptedProposal) -> AcceptedResponse:
        acceptance = accepted.acceptance
        return cls(
            run_id=accepted.run.id, architecture_id=acceptance.architecture_id,
            revision=acceptance.revision_number, content_hash=acceptance.content_hash,
            created_architecture=accepted.created_architecture, created_revision=accepted.created_revision,
        )  # fmt: skip


class LimitationModel(ApiModel):
    code: str
    message: str


class FieldDifferenceModel(ApiModel):
    field: str
    before: Any
    after: Any
    change: Change


class DifferenceModel(ApiModel):
    element: str
    subject: str
    change: Change
    fields: list[FieldDifferenceModel]


class RunComparisonResponse(ApiModel):
    version: int
    earlier: str = Field(description="The earlier run's result fingerprint.")
    later: str
    identical: bool
    comparability: Comparability
    limitations: list[LimitationModel]
    compared_artifacts: list[str]
    differences: list[DifferenceModel] = Field(
        description="Differences of what the sources declare — not runtime drift; none when not comparable."
    )
    fingerprint: str

    @classmethod
    def of(cls, comparison: ResultComparison) -> RunComparisonResponse:
        return cls.model_validate(comparison.to_dict())


class BaselineComparisonResponse(ApiModel):
    version: int
    result: str
    baseline_content_hash: str
    proposal_content_hash: str
    comparability: Comparability
    limitations: list[LimitationModel]
    differences: list[DifferenceModel] = Field(
        description="not_in_sources: the sources do not describe it (unknown), never removed."
    )
    fingerprint: str

    @classmethod
    def of(cls, comparison: BaselineComparison) -> BaselineComparisonResponse:
        return cls.model_validate(comparison.to_dict())
