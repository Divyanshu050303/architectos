"""Security analyses over HTTP: the request (revision, scope, analyzers, assumptions) and the stored
analysis, typed field by field. Findings are architecture-level observations for engineering
review: never a score, never a claim that the architecture is secure or compliant; an unknown
control is reported as not modeled, never as present or absent. No secret value is ever returned."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from core.domain.capacity.results import Certainty
from core.domain.security.analyses import MAX_ANALYZERS, MAX_ASSUMPTIONS, MAX_SCOPE, SecurityAssumption
from core.domain.security.reports import SecurityReport
from core.domain.security.results import (
    CheckSource,
    Condition,
    Coverage,
    FindingBasis,
    FindingCategory,
    FindingType,
    StrideCategory,
)
from core.domain.validation.results import Severity, Verdict

from .capacity import (
    AnalysisErrorModel,
    EvidenceModel,
    LimitationModel,
    ModelRefModel,
    ModelSetModel,
    UnsupportedModel,
)
from .common import ApiModel, RequestModel

type NodeId = Annotated[str, Field(min_length=1, max_length=128)]
type AnalyzerId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]{0,63}$")]

# --- request -------------------------------------------------------------------------------------


class SecurityAssumptionInput(RequestModel):
    key: Annotated[str, Field(min_length=1, max_length=64, examples=["waf"])]
    statement: Annotated[str, Field(min_length=1, max_length=500)]

    def to_domain(self) -> SecurityAssumption:
        return SecurityAssumption(self.key, self.statement)


class RunSecurityAnalysisRequest(RequestModel):
    """Everything is optional: the security inputs are the architecture's own properties, and the
    policy and requirements are the project's."""

    revision: Annotated[int | None, Field(ge=1, description="Default: the current revision.")] = None
    scope: Annotated[list[NodeId], Field(max_length=MAX_SCOPE)] = Field(
        default_factory=list, description="Components to analyze (with what touches them); default: all."
    )
    analyzers: Annotated[list[AnalyzerId] | None, Field(max_length=MAX_ANALYZERS)] = Field(
        default=None, description="Analyzers to run (GET /security/analyzers); default: all."
    )
    assumptions: Annotated[list[SecurityAssumptionInput], Field(max_length=MAX_ASSUMPTIONS)] = Field(
        default_factory=list, description="Recorded with the analysis, never computed with."
    )
    label: Annotated[str | None, Field(min_length=1, max_length=100)] = None


# --- responses -----------------------------------------------------------------------------------


class SecurityFindingModel(ApiModel):
    id: str = Field(description="Stable: the same finding has the same id in every analysis.")
    type: FindingType
    category: FindingCategory
    basis: FindingBasis = Field(
        description="control_gap: a control declared absent; potential_risk: the model could allow harm; "
        "violation: a requirement or policy contradicted; not_evaluable: not modeled enough to decide."
    )
    severity: Severity
    certainty: Certainty = Field(
        description="modeled: established by declared facts; candidate: inferred, proposed or incomplete."
    )
    title: str
    explanation: str
    recommendation: str = Field(description="For engineering review, never an automatic change.")
    node_ids: list[str]
    connection_ids: list[str]
    boundary_ids: list[str] = Field(description="The trust zones it concerns.")
    evidence: list[EvidenceModel] = Field(
        description="Declared facts with provenance; secrets are '[redacted]'."
    )
    assumptions: list[str]
    missing: list[str] = Field(description="What the architecture would need to declare to decide.")
    analyzer_id: str | None
    analyzer_version: int | None
    threat: StrideCategory | None = Field(description="STRIDE category of a threat candidate.")
    requirement_id: str | None
    policy_rule: str | None


class SecurityFindingPage(ApiModel):
    findings: list[SecurityFindingModel]
    next_cursor: str | None


class SecurityComponentModel(ApiModel):
    node_id: str
    coverage: Coverage = Field(description="Whether the security properties that matter for it are modeled.")
    exposure: str | None = Field(description="As declared; null when not modeled.")
    sensitive: bool | None = Field(
        description="By declared classification or personal data; null: not established."
    )
    trust_zone_ids: list[str]
    inputs: list[EvidenceModel] = Field(description="The security facts it declares, with provenance.")
    missing: list[str]


class SecurityComponentPage(ApiModel):
    components: list[SecurityComponentModel]
    next_cursor: str | None


class TrustZoneModel(ApiModel):
    boundary_id: str
    trust_level: str | None = Field(description="null when the zone's trust level is not modeled.")
    node_ids: list[str]


class CheckModel(ApiModel):
    key: str
    source: CheckSource
    condition: Condition
    verdict: Verdict = Field(
        description="satisfied or violated by modeled evidence only; not_verifiable when it cannot be "
        "decided or the requirement is unsupported (never a pass); not_applicable when nothing is concerned."
    )
    explanation: str
    node_ids: list[str]
    connection_ids: list[str]
    actual: list[EvidenceModel]
    missing: list[str]
    requirement_id: str | None
    policy_rule: str | None
    mapping: str | None = Field(description="How a requirement's words became the condition.")


class SecuritySummaryModel(ApiModel):
    """Counts and coverage; no score."""

    components: dict[str, int] = Field(description="By coverage.")
    findings: dict[str, int] = Field(description="By severity.")
    bases: dict[str, int]
    categories: dict[str, int]
    threats: dict[str, int] = Field(description="Threat candidates by STRIDE category.")
    checks: dict[str, int] = Field(description="By verdict.")
    trust_zones: int
    unsupported: int


class SecurityAnalysisSummary(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    revision: int
    revision_content_hash: str
    label: str | None
    status: str = Field(description="completed, partial, insufficient_input, unsupported or failed.")
    requested_by_user_id: uuid.UUID | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result_fingerprint: str | None
    summary: SecuritySummaryModel | None
    error: AnalysisErrorModel | None

    @classmethod
    def fields_for(cls, report: SecurityReport) -> dict[str, Any]:
        a = report.analysis
        return {
            "id": a.id,
            "project_id": a.project_id,
            "architecture_id": a.architecture_id,
            "revision": a.revision_number,
            "revision_content_hash": a.revision_content_hash,
            "label": a.label,
            "status": a.status,
            "requested_by_user_id": a.requested_by_user_id,
            "requested_at": a.requested_at,
            "started_at": a.started_at,
            "completed_at": a.completed_at,
            "result_fingerprint": report.result_fingerprint,
            "summary": SecuritySummaryModel.model_validate(report.summary)
            if report.summary is not None
            else None,
            "error": AnalysisErrorModel(code=a.error.code, message=a.error.message) if a.error else None,
        }

    @classmethod
    def of(cls, report: SecurityReport) -> SecurityAnalysisSummary:
        return cls(**cls.fields_for(report))


class SecurityAnalysisResponse(SecurityAnalysisSummary):
    """The analysis without its components and findings (GET …/components, …/findings)."""

    inputs: dict[str, Any] = Field(
        description="The request as stored, the project's policy as it was, and the requirements read."
    )
    analyzer_set: ModelSetModel | None
    context_fingerprint: str | None
    trust_zones: list[TrustZoneModel]
    checks: list[CheckModel]
    unsupported: list[UnsupportedModel]
    limitations: list[LimitationModel]

    @classmethod
    def of(cls, report: SecurityReport) -> SecurityAnalysisResponse:
        analyzer_set = report.analyzer_set
        return cls(
            **cls.fields_for(report),
            inputs=dict(report.inputs),
            analyzer_set=ModelSetModel(
                version=analyzer_set.version,
                models=[ModelRefModel(id=m[0], version=m[1]) for m in analyzer_set.models],
            )
            if analyzer_set
            else None,
            context_fingerprint=report.context_fingerprint,
            trust_zones=[TrustZoneModel.model_validate(z.to_dict()) for z in report.trust_zones],
            checks=[CheckModel.model_validate(c.to_dict()) for c in report.checks],
            unsupported=[UnsupportedModel.model_validate(u.to_dict()) for u in report.unsupported],
            limitations=[LimitationModel.model_validate(x.to_dict()) for x in report.limitations],
        )


class SecurityAnalysisPage(ApiModel):
    analyses: list[SecurityAnalysisSummary]
    next_cursor: str | None


class SecurityAnalyzerModel(ApiModel):
    """An analyzer, in run order: what it reads, its rules in words, what it cannot evaluate."""

    id: str
    version: int
    name: str
    description: str
    category: FindingCategory
    finding_types: list[FindingType]
    inputs: list[str]
    properties: list[str]
    rules: list[str]
    produces: list[str]
    requires: list[str]
    unsupported: list[str]
    limitations: list[str]


class SecurityAnalyzerCatalog(ApiModel):
    analyzers: list[SecurityAnalyzerModel]
