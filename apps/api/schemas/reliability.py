"""Reliability analyses over HTTP: the request (revision, entries, objectives, assumptions) and the
stored analysis, typed field by field. Availability is a fraction (0.999 = 99.9 %) as an exact
decimal string; durations carry their unit; an unknown value is null, never 0 or 1."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from core.domain.capacity.results import Certainty, ComponentStatus
from core.domain.capacity.units import exact
from core.domain.reliability.analyses import (
    MAX_ASSUMPTIONS,
    MAX_ENTRIES,
    MAX_OBJECTIVES,
    MAX_SCOPE,
    Objective,
    ReliabilityAssumption,
)
from core.domain.reliability.reports import ReliabilityReport
from core.domain.reliability.results import ComponentResult, FindingType, ObjectiveKind
from core.domain.validation.results import Severity, Verdict

from .capacity import (
    AnalysisErrorModel,
    EstimateModel,
    EvidenceModel,
    LimitationModel,
    ModelRefModel,
    ModelSetModel,
    Number,
    QuantityInput,
    UnsupportedModel,
)
from .common import ApiModel, RequestModel

type NodeId = Annotated[str, Field(min_length=1, max_length=128)]

# --- request -------------------------------------------------------------------------------------


class ObjectiveInput(RequestModel):
    """``availability`` and ``redundancy`` take a ``target`` (a fraction, a count); ``recovery_time``
    and ``data_loss`` a ``duration``. Empty ``nodeIds``: the whole architecture."""

    key: Annotated[str, Field(min_length=1, max_length=64, examples=["checkout-slo"])]
    kind: ObjectiveKind
    target: Number | None = None
    duration: QuantityInput | None = None
    node_ids: Annotated[list[NodeId], Field(max_length=MAX_SCOPE)] = Field(default_factory=list)
    strict: bool = Field(default=False, description="More than / less than, rather than at least / at most.")

    def to_domain(self) -> Objective:
        target = exact(self.target, "objectives.target") if self.target is not None else None
        duration = self.duration.to_domain("objectives.duration") if self.duration is not None else None
        return Objective(self.key, self.kind, target, duration, tuple(self.node_ids), strict=self.strict)


class ReliabilityAssumptionInput(RequestModel):
    key: Annotated[str, Field(min_length=1, max_length=64, examples=["zones_independent"])]
    statement: Annotated[str, Field(min_length=1, max_length=500)]

    def to_domain(self) -> ReliabilityAssumption:
        return ReliabilityAssumption(self.key, self.statement)


class RunReliabilityAnalysisRequest(RequestModel):
    """Everything is optional: the component inputs are the architecture's own properties."""

    revision: Annotated[int | None, Field(ge=1, description="Default: the current revision.")] = None
    entries: Annotated[list[NodeId] | None, Field(max_length=MAX_ENTRIES)] = Field(
        default=None, description="Where requests enter; default: every client."
    )
    objectives: Annotated[list[ObjectiveInput], Field(max_length=MAX_OBJECTIVES)] = Field(
        default_factory=list
    )
    assumptions: Annotated[list[ReliabilityAssumptionInput], Field(max_length=MAX_ASSUMPTIONS)] = Field(
        default_factory=list
    )
    label: Annotated[str | None, Field(min_length=1, max_length=100)] = None


# --- responses -----------------------------------------------------------------------------------


class PathModel(ApiModel):
    entry_id: str
    node_ids: list[str] = Field(
        description="What the entry's requests require, the entry first: the analyzed scope."
    )
    connection_ids: list[str] = Field(description="The required connections followed.")
    optional_connection_ids: list[str] = Field(
        description="Asynchronous or non-critical: recorded, not required."
    )
    availability: EstimateModel
    complete: bool = Field(description="False while any required component's availability is unknown.")


class ObjectiveResultModel(ApiModel):
    key: str
    kind: ObjectiveKind
    target: str = Field(description='The objective as a bound, e.g. ">= 0.999", "<= 15 min".')
    verdict: Verdict = Field(
        description="satisfied or violated by modeled evidence only; else not_verifiable."
    )
    explanation: str
    node_ids: list[str]
    actual: list[EvidenceModel] = Field(description="The modeled values it was compared with.")
    missing: list[str]
    requirement_id: str | None


class FindingModel(ApiModel):
    id: str = Field(description="Stable: the same finding has the same id in every analysis.")
    type: FindingType
    severity: Severity
    certainty: Certainty = Field(
        description="modeled: established by declared facts; candidate: worth a look."
    )
    title: str
    explanation: str
    recommendation: str = Field(description="Options for human review, never an automatic change.")
    node_ids: list[str]
    connection_ids: list[str]
    evidence: list[EvidenceModel]
    assumptions: list[str]
    missing: list[str]
    model_id: str | None
    model_version: int | None
    objective: str | None


class FindingPage(ApiModel):
    findings: list[FindingModel]
    next_cursor: str | None


class ReliabilityComponentModel(ApiModel):
    node_id: str
    status: ComponentStatus
    models: list[ModelRefModel]
    estimates: list[EstimateModel] = Field(
        description="availability, replica_availability, recovery_time, data_loss_window."
    )
    inputs: list[EvidenceModel] = Field(
        description="The reliability facts the component declares, with provenance."
    )
    missing: list[str]

    @classmethod
    def of(cls, component: ComponentResult) -> ReliabilityComponentModel:
        data = component.to_dict()
        return cls.model_validate(data | {"models": [{"id": m[0], "version": m[1]} for m in data["models"]]})


class ReliabilityComponentPage(ApiModel):
    components: list[ReliabilityComponentModel]
    next_cursor: str | None


class ReliabilitySummaryModel(ApiModel):
    """Counts and coverage; no score and no architecture-wide availability."""

    components: dict[str, int]
    findings: dict[str, int] = Field(description="By severity.")
    objectives: dict[str, int] = Field(description="By verdict.")
    paths: int
    paths_estimated: int
    unsupported: int


class ReliabilityAnalysisSummary(ApiModel):
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
    summary: ReliabilitySummaryModel | None
    error: AnalysisErrorModel | None

    @classmethod
    def fields_for(cls, report: ReliabilityReport) -> dict[str, Any]:
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
            "summary": ReliabilitySummaryModel.model_validate(report.summary)
            if report.summary is not None
            else None,
            "error": AnalysisErrorModel(code=a.error.code, message=a.error.message) if a.error else None,
        }

    @classmethod
    def of(cls, report: ReliabilityReport) -> ReliabilityAnalysisSummary:
        return cls(**cls.fields_for(report))


class ReliabilityAnalysisResponse(ReliabilityAnalysisSummary):
    """The analysis without its components and findings (GET …/components, …/findings)."""

    inputs: dict[str, Any] = Field(description="The request as stored, and the requirements it read.")
    model_set: ModelSetModel | None
    context_fingerprint: str | None
    paths: list[PathModel]
    objectives: list[ObjectiveResultModel]
    unsupported: list[UnsupportedModel]
    limitations: list[LimitationModel]

    @classmethod
    def of(cls, report: ReliabilityReport) -> ReliabilityAnalysisResponse:
        model_set = report.model_set
        return cls(
            **cls.fields_for(report),
            inputs=dict(report.inputs),
            model_set=ModelSetModel(
                version=model_set.version,
                models=[ModelRefModel(id=m[0], version=m[1]) for m in model_set.models],
            )
            if model_set
            else None,
            context_fingerprint=report.context_fingerprint,
            paths=[PathModel.model_validate(p.to_dict()) for p in report.paths],
            objectives=[ObjectiveResultModel.model_validate(o.to_dict()) for o in report.objectives],
            unsupported=[UnsupportedModel.model_validate(u.to_dict()) for u in report.unsupported],
            limitations=[LimitationModel.model_validate(x.to_dict()) for x in report.limitations],
        )


class ReliabilityAnalysisPage(ApiModel):
    analyses: list[ReliabilityAnalysisSummary]
    next_cursor: str | None


class ReliabilityModelModel(ApiModel):
    """A component model (``type: model``, in precedence order) or an architecture step (``step``)."""

    type: str
    id: str
    version: int
    name: str
    description: str
    kinds: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    configuration: list[str] = Field(default_factory=list)
    assumptions: list[str]
    formula: str | None = None
    unsupported: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    limitations: list[str]


class ReliabilityModelCatalog(ApiModel):
    models: list[ReliabilityModelModel]
