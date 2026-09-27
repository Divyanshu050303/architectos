"""Observability analyses over HTTP: the request (revision, scope, analyzers, requirement ids,
assumptions) and the stored analysis, typed field by field. Everything is architecture-level: what
the architecture declares about logs, metrics, traces, health checks and alerting, never telemetry.
A modeled capability is not proof it works in production; a satisfied objective check is a traceable
indicator, never an attained objective. Counts only: no score, no percentage."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from core.domain.capacity.results import Certainty
from core.domain.observability.analyses import (
    MAX_ANALYZERS,
    MAX_ASSUMPTIONS,
    MAX_REQUIREMENTS,
    MAX_SCOPE,
    ObservabilityAssumption,
)
from core.domain.observability.reports import ObservabilityReport
from core.domain.observability.results import Condition, FindingBasis, FindingCategory, FindingType
from core.domain.observability.values import CoverageState, Dimension
from core.domain.validation.results import Severity

from .capacity import (
    AnalysisErrorModel,
    EvidenceModel,
    LimitationModel,
    ModelRefModel,
    ModelSetModel,
    UnsupportedModel,
)
from .checks import CheckBaseModel
from .common import ApiModel, RequestModel
from .security import AnalyzerId, NodeId

# --- request -------------------------------------------------------------------------------------


class ObservabilityAssumptionInput(RequestModel):
    key: Annotated[str, Field(min_length=1, max_length=64, examples=["collector-ha"])]
    statement: Annotated[str, Field(min_length=1, max_length=500)]

    def to_domain(self) -> ObservabilityAssumption:
        return ObservabilityAssumption(self.key, self.statement)


class RunObservabilityAnalysisRequest(RequestModel):
    """Everything is optional: the observability inputs are the architecture's own properties, and
    the policy and requirements are the project's."""

    revision: Annotated[int | None, Field(ge=1, description="Default: the current revision.")] = None
    scope: Annotated[list[NodeId], Field(max_length=MAX_SCOPE)] = Field(
        default_factory=list, description="Components to analyze (with what touches them); default: all."
    )
    analyzers: Annotated[list[AnalyzerId] | None, Field(max_length=MAX_ANALYZERS)] = Field(
        default=None, description="Analyzers to run (GET /observability/analyzers); default: all."
    )
    requirement_ids: Annotated[list[uuid.UUID] | None, Field(max_length=MAX_REQUIREMENTS)] = Field(
        default=None, description="In-force requirements to evaluate; default: all in force."
    )
    assumptions: Annotated[list[ObservabilityAssumptionInput], Field(max_length=MAX_ASSUMPTIONS)] = Field(
        default_factory=list, description="Recorded with the analysis, never computed with."
    )
    label: Annotated[str | None, Field(min_length=1, max_length=100)] = None


# --- responses -----------------------------------------------------------------------------------


class ObservabilityFindingModel(ApiModel):
    id: str = Field(description="Stable: the same finding has the same id in every analysis.")
    type: FindingType
    category: FindingCategory
    basis: FindingBasis = Field(
        description="control_gap: a capability declared off where it matters; potential_risk: the model "
        "could hide or leak something; violation: a requirement or policy contradicted; not_evaluable: "
        "not modeled enough to decide."
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
    evidence: list[EvidenceModel] = Field(
        description="Declared facts with provenance; secrets are '[redacted]'."
    )
    assumptions: list[str]
    missing: list[str] = Field(description="What the architecture would need to declare to decide.")
    analyzer_id: str | None
    analyzer_version: int | None
    dimension: Dimension | None = Field(description="The signal it is about, if one.")
    requirement_id: str | None
    policy_rule: str | None
    check_key: str | None = Field(description="The check a requirement or policy finding reports.")


class ObservabilityFindingPage(ApiModel):
    findings: list[ObservabilityFindingModel]
    next_cursor: str | None


class ObservabilityComponentModel(ApiModel):
    node_id: str
    criticality: str | None = Field(description="critical or standard, as declared; null: not modeled.")
    coverage: dict[Dimension, CoverageState] = Field(
        description="Per dimension: modeled, partial, absent, unknown (not declared) or unsupported."
    )
    inputs: list[EvidenceModel] = Field(description="The observability facts it declares, with provenance.")
    missing: list[str]


class ObservabilityComponentPage(ApiModel):
    components: list[ObservabilityComponentModel]
    next_cursor: str | None


class ObservabilityCheckModel(CheckBaseModel):
    condition: Condition


class ObjectivesModel(ApiModel):
    measurable: dict[str, int] = Field(description="objective_measurable checks by verdict.")
    alerted: dict[str, int] = Field(description="objective_alerted checks by verdict.")
    unsupported_requirements: int = Field(description="Requirement checks with no supported condition.")


class ObservabilitySummaryModel(ApiModel):
    """Counts only, reproducible from the components, findings and checks; no score, no percentage."""

    scope: dict[str, int] = Field(description="components, eligible, unsupported (third parties).")
    components: int
    criticality: dict[str, int] = Field(description="critical, standard, not_modeled.")
    coverage: dict[str, dict[str, int]] = Field(description="Per dimension, components by coverage state.")
    critical_coverage: dict[str, dict[str, int]] = Field(description="The same, for critical components.")
    findings: dict[str, int] = Field(description="By severity.")
    bases: dict[str, int]
    categories: dict[str, int]
    checks: dict[str, int] = Field(description="By verdict.")
    collection: dict[str, dict[str, int]] = Field(
        description="Per signal: components declaring it, with and without a modeled collection path."
    )
    objectives: ObjectivesModel
    priorities: list[str] = Field(description="The first finding ids in priority order.")
    unsupported: int


class ObservabilityAnalysisSummary(ApiModel):
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
    summary: ObservabilitySummaryModel | None
    error: AnalysisErrorModel | None

    @classmethod
    def fields_for(cls, report: ObservabilityReport) -> dict[str, Any]:
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
            "summary": ObservabilitySummaryModel.model_validate(report.summary)
            if report.summary is not None
            else None,
            "error": AnalysisErrorModel(code=a.error.code, message=a.error.message) if a.error else None,
        }

    @classmethod
    def of(cls, report: ObservabilityReport) -> ObservabilityAnalysisSummary:
        return cls(**cls.fields_for(report))


class ObservabilityAnalysisResponse(ObservabilityAnalysisSummary):
    """The analysis without its components and findings (GET …/components, …/findings)."""

    inputs: dict[str, Any] = Field(
        description="The request as stored, the project's policy as it was, and the requirements read."
    )
    analyzer_set: ModelSetModel | None
    context_fingerprint: str | None
    checks: list[ObservabilityCheckModel]
    unsupported: list[UnsupportedModel]
    limitations: list[LimitationModel]

    @classmethod
    def of(cls, report: ObservabilityReport) -> ObservabilityAnalysisResponse:
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
            checks=[ObservabilityCheckModel.model_validate(c.to_dict()) for c in report.checks],
            unsupported=[UnsupportedModel.model_validate(u.to_dict()) for u in report.unsupported],
            limitations=[LimitationModel.model_validate(x.to_dict()) for x in report.limitations],
        )


class ObservabilityAnalysisPage(ApiModel):
    analyses: list[ObservabilityAnalysisSummary]
    next_cursor: str | None


class ObservabilityAnalyzerModel(ApiModel):
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


class ObservabilityAnalyzerCatalog(ApiModel):
    analyzers: list[ObservabilityAnalyzerModel]
