"""Capacity analyses over HTTP: the request (revision, workload, models, scenarios) and the stored
analysis, typed field by field. Numbers are exact decimal strings in responses; requests accept
numbers or decimal strings, always with an explicit unit where one applies."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import Field, StrictInt

from core.domain.capacity.analyses import AnalysisReport
from core.domain.capacity.results import (
    AnalysisStatus,
    BottleneckCondition,
    Certainty,
    ComponentStatus,
    Source,
    UtilizationState,
)
from core.domain.capacity.scenarios import (
    MAX_CHANGES,
    MAX_SCENARIOS,
    ConfigurationChange,
    ScalingKind,
    Scenario,
)
from core.domain.capacity.units import Quantity, exact
from core.domain.capacity.workload import (
    MAX_ASSUMPTIONS,
    MAX_REQUIREMENTS,
    WorkloadAssumption,
    WorkloadProfile,
    WorkloadType,
)

from .common import ApiModel, RequestModel

type Number = Annotated[str, Field(max_length=40)] | StrictInt | float
type Identifier = Annotated[str, Field(min_length=1, max_length=128)]

# --- request -------------------------------------------------------------------------------------


class QuantityInput(RequestModel):
    value: Number
    unit: Annotated[str, Field(min_length=1, max_length=32, examples=["requests/second", "KB", "s"])]

    def to_domain(self, field: str) -> Quantity:
        return Quantity.of(self.value, self.unit, field)


def _quantity(model: QuantityInput | None, field: str) -> Quantity | None:
    return model.to_domain(field) if model is not None else None


def _number(value: Number | None, field: str) -> Decimal | None:
    return exact(value, field) if value is not None else None


class AssumptionInput(RequestModel):
    key: Annotated[str, Field(min_length=1, max_length=64, examples=["cache_hit_ratio"])]
    statement: Annotated[str, Field(min_length=1, max_length=500)]
    value: QuantityInput | None = None

    def to_domain(self) -> WorkloadAssumption:
        return WorkloadAssumption(self.key, self.statement, _quantity(self.value, "assumptions.value"))


class WorkloadInput(RequestModel):
    """Only the fields the workload type needs (see docs/api/capacity.md)."""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    type: WorkloadType
    description: Annotated[str | None, Field(max_length=1000)] = None
    peak_rate: QuantityInput | None = None
    average_rate: QuantityInput | None = None
    peak_duration: QuantityInput | None = None
    concurrent_users: StrictInt | None = None
    concurrent_connections: StrictInt | None = None
    read_ratio: Number | None = None
    request_payload: QuantityInput | None = None
    response_payload: QuantityInput | None = None
    batch_size: StrictInt | None = None
    batch_interval: QuantityInput | None = None
    growth: Number | None = None
    target_utilization: Number | None = None
    assumptions: Annotated[list[AssumptionInput], Field(max_length=MAX_ASSUMPTIONS)] = Field(
        default_factory=list
    )
    requirement_ids: Annotated[list[uuid.UUID], Field(max_length=MAX_REQUIREMENTS)] = Field(
        default_factory=list
    )

    def to_domain(self) -> WorkloadProfile:
        return WorkloadProfile(
            name=self.name,
            type=self.type,
            description=self.description,
            peak_rate=_quantity(self.peak_rate, "peak_rate"),
            average_rate=_quantity(self.average_rate, "average_rate"),
            peak_duration=_quantity(self.peak_duration, "peak_duration"),
            concurrent_users=self.concurrent_users,
            concurrent_connections=self.concurrent_connections,
            read_ratio=_number(self.read_ratio, "read_ratio"),
            request_payload=_quantity(self.request_payload, "request_payload"),
            response_payload=_quantity(self.response_payload, "response_payload"),
            batch_size=self.batch_size,
            batch_interval=_quantity(self.batch_interval, "batch_interval"),
            growth=_number(self.growth, "growth"),
            target_utilization=_number(self.target_utilization, "target_utilization"),
            assumptions=tuple(a.to_domain() for a in self.assumptions),
            requirement_ids=tuple(self.requirement_ids),
        )


class ChangeInput(RequestModel):
    element_id: Identifier
    property: Annotated[str, Field(min_length=1, max_length=64, examples=["replicas"])]
    value: Number


class ScenarioInput(RequestModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    growth: Number | None = None
    growth_rate: Number | None = None
    periods: StrictInt | None = None
    target_rate: QuantityInput | None = None
    target_utilization: Number | None = None
    changes: Annotated[list[ChangeInput], Field(max_length=MAX_CHANGES)] = Field(default_factory=list)

    def to_domain(self) -> Scenario:
        return Scenario(
            name=self.name,
            growth=_number(self.growth, "growth"),
            growth_rate=_number(self.growth_rate, "growth_rate"),
            periods=self.periods,
            target_rate=_quantity(self.target_rate, "target_rate"),
            target_utilization=_number(self.target_utilization, "target_utilization"),
            changes=tuple(
                ConfigurationChange(c.element_id, c.property, exact(c.value, "changes.value"))
                for c in self.changes
            ),
        )


type ParameterValue = bool | StrictInt | Annotated[str, Field(max_length=200)]
type ParameterName = Annotated[str, Field(max_length=64)]
type ModelParameters = Annotated[dict[ParameterName, ParameterValue], Field(max_length=20)]


class RunCapacityAnalysisRequest(RequestModel):
    """The workload is required; everything else is optional. Model ids and entry node ids are
    given as they are (not camelCased)."""

    revision: Annotated[int | None, Field(ge=1, description="Default: the current revision.")] = None
    workload: WorkloadInput
    models: Annotated[list[Annotated[str, Field(max_length=64)]] | None, Field(max_length=50)] = None
    parameters: Annotated[dict[ParameterName, ModelParameters], Field(max_length=50)] = Field(
        default_factory=dict
    )
    assumptions: Annotated[list[AssumptionInput], Field(max_length=MAX_ASSUMPTIONS)] = Field(
        default_factory=list
    )
    entries: Annotated[list[Identifier] | None, Field(max_length=50, description="Default: the clients.")] = (
        None
    )
    label: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    scenarios: Annotated[list[ScenarioInput], Field(max_length=MAX_SCENARIOS)] = Field(default_factory=list)


# --- response ------------------------------------------------------------------------------------


class QuantityModel(ApiModel):
    value: str = Field(description='Exact decimal, e.g. "1500.5".')
    unit: str


class EvidenceModel(ApiModel):
    label: str
    value: str


class EstimateModel(ApiModel):
    element_id: str
    resource: str
    quantity: QuantityModel | None = Field(description="null when the value is unknown (never 0 instead).")
    source: Source
    basis: str = Field(description="The formula, or the property the value was declared in.")
    model_id: str | None
    model_version: int | None
    inputs: list[EvidenceModel]
    missing: list[str] = Field(description="What an unknown value lacks, e.g. configuration.replicas.")


class DemandModel(ApiModel):
    element_id: str
    resource: str
    quantity: QuantityModel
    path: list[str] = Field(description="[upstream node, connection, node]; [node] at an entry.")
    factors: list[EvidenceModel]
    source: Source


class UtilizationModel(ApiModel):
    resource: str
    demand: QuantityModel | None
    capacity: QuantityModel | None
    target: str | None
    state: UtilizationState
    ratio: str | None = Field(description="demand / capacity, never clamped (1.5 = 50 % over).")
    headroom: str | None = Field(description="capacity - demand, canonical unit; negative when exceeded.")
    relative_headroom: str | None
    headroom_to_target: str | None


class ModelRefModel(ApiModel):
    id: str
    version: int


class ComponentModel(ApiModel):
    node_id: str
    status: ComponentStatus
    models: list[ModelRefModel]
    demand: list[DemandModel]
    limits: list[EstimateModel]
    resources: list[EstimateModel]
    utilization: list[UtilizationModel]
    missing: list[str]
    notes: list[str]

    @classmethod
    def of(cls, data: dict[str, Any]) -> ComponentModel:
        return cls.model_validate(data | {"models": [{"id": m[0], "version": m[1]} for m in data["models"]]})


class ComponentPage(ApiModel):
    components: list[ComponentModel]
    next_cursor: str | None


class BottleneckModel(ApiModel):
    node_id: str
    resource: str
    condition: BottleneckCondition
    certainty: Certainty = Field(
        description="modeled: demand and capacity known; candidate: evidence incomplete."
    )
    utilization: UtilizationModel
    explanation: str
    remediation: str
    evidence: list[EvidenceModel]
    assumptions: list[str]


class BottleneckList(ApiModel):
    bottlenecks: list[BottleneckModel]


class UnsupportedModel(ApiModel):
    element_id: str
    code: str
    message: str
    missing: list[str]


class LimitationModel(ApiModel):
    code: str
    message: str


class SummaryModel(ApiModel):
    """Counts and two derived figures; no score."""

    components: dict[str, int]
    bottlenecks: dict[str, int]
    unsupported: int
    highest_utilization: str | None
    saturation_multiple: str | None = Field(
        description="The workload multiple at which the first known limit is reached."
    )
    saturation_complete: bool = Field(
        description="True only when every component the workload reaches has a known throughput capacity."
    )


class ModelSetModel(ApiModel):
    version: str
    models: list[ModelRefModel]


class ScalingOptionModel(ApiModel):
    node_id: str
    resource: str
    kind: ScalingKind
    current: QuantityModel
    required: QuantityModel
    basis: str
    model_id: str
    evidence: list[EvidenceModel]


class ResourceDeltaModel(ApiModel):
    node_id: str
    resource: str
    demand_before: str | None
    demand_after: str | None
    capacity_before: str | None
    capacity_after: str | None
    utilization_before: str | None
    utilization_after: str | None


class BottleneckKeyModel(ApiModel):
    node_id: str
    resource: str
    condition: BottleneckCondition


class ComparisonModel(ApiModel):
    changed_inputs: list[EvidenceModel]
    changed_configuration: list[EvidenceModel]
    resources: list[ResourceDeltaModel]
    new_bottlenecks: list[BottleneckKeyModel]
    resolved_bottlenecks: list[BottleneckKeyModel]


class ChangeModel(ApiModel):
    element_id: str
    property: str
    value: str


class ScenarioEchoModel(ApiModel):
    name: str
    growth: str | None
    growth_rate: str | None
    periods: int | None
    target_rate: QuantityModel | None
    target_utilization: str | None
    changes: list[ChangeModel]


class ScenarioModel(ApiModel):
    scenario: ScenarioEchoModel
    status: AnalysisStatus
    summary: SummaryModel
    result_fingerprint: str
    bottlenecks: list[BottleneckModel]
    scaling: list[ScalingOptionModel]
    unsupported_scaling: list[UnsupportedModel]
    comparison: ComparisonModel


class ScenarioList(ApiModel):
    scenarios: list[ScenarioModel]


class AnalysisErrorModel(ApiModel):
    code: str
    message: str


def _keys(items: list[list[str]]) -> list[dict[str, str]]:
    return [{"node_id": k[0], "resource": k[1], "condition": k[2]} for k in items]


def scenario_model(data: dict[str, Any]) -> ScenarioModel:
    comparison = data["comparison"] | {
        "new_bottlenecks": _keys(data["comparison"]["new_bottlenecks"]),
        "resolved_bottlenecks": _keys(data["comparison"]["resolved_bottlenecks"]),
    }
    return ScenarioModel.model_validate(data | {"comparison": comparison})


class CapacityAnalysisSummary(ApiModel):
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
    summary: SummaryModel | None
    error: AnalysisErrorModel | None

    @classmethod
    def fields_for(cls, report: AnalysisReport) -> dict[str, Any]:
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
            "summary": SummaryModel.model_validate(report.summary.to_dict()) if report.summary else None,
            "error": AnalysisErrorModel(code=a.error.code, message=a.error.message) if a.error else None,
        }

    @classmethod
    def of(cls, report: AnalysisReport) -> CapacityAnalysisSummary:
        return cls(**cls.fields_for(report))


class CapacityAnalysisResponse(CapacityAnalysisSummary):
    """The analysis without its components and bottlenecks (GET …/components, …/bottlenecks)."""

    inputs: dict[str, Any] = Field(
        description="The request as stored: workload snapshot, models, parameters, assumptions, entries."
    )
    model_set: ModelSetModel | None
    context_fingerprint: str | None
    connections: list[DemandModel] = Field(description="Demand carried by each connection.")
    unsupported: list[UnsupportedModel]
    limitations: list[LimitationModel]
    scaling: list[ScalingOptionModel]
    unsupported_scaling: list[UnsupportedModel]
    scenarios: list[ScenarioModel]

    @classmethod
    def of(cls, report: AnalysisReport) -> CapacityAnalysisResponse:
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
            connections=[DemandModel.model_validate(d.to_dict()) for d in report.connections],
            unsupported=[UnsupportedModel.model_validate(u.to_dict()) for u in report.unsupported],
            limitations=[LimitationModel.model_validate(x.to_dict()) for x in report.limitations],
            scaling=[ScalingOptionModel.model_validate(o.to_dict()) for o in report.scaling],
            unsupported_scaling=[
                UnsupportedModel.model_validate(u.to_dict()) for u in report.unsupported_scaling
            ],
            scenarios=[scenario_model(s.to_dict()) for s in report.scenarios],
        )


class CapacityAnalysisPage(ApiModel):
    analyses: list[CapacityAnalysisSummary]
    next_cursor: str | None


class ModelParameterModel(ApiModel):
    type: str
    description: str
    default: Any


class CapacityModelModel(ApiModel):
    id: str
    version: int
    name: str
    description: str
    kinds: list[str] = Field(description="The node kinds it applies to.")
    resources: list[str] = Field(description="What it estimates.")
    configuration: list[str] = Field(description="Configuration properties it requires.")
    workload: list[str] = Field(description="Workload fields it requires.")
    assumptions: list[str] = Field(description="Assumption keys it requires.")
    parameters: dict[str, ModelParameterModel]
    limitations: list[str]


class CapacityModelCatalog(ApiModel):
    models: list[CapacityModelModel]
