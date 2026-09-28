"""HTTP models of the component catalog: categories, entries, specifications (every claim with its
provenance), versions, and the evaluation of a configuration against a specification."""

import datetime as dt
from typing import Any, Self

from pydantic import Field

from apps.api.schemas.common import ApiModel, RequestModel
from core.architecture_ir.component import NodeKind
from core.domain.components.evaluation import ConstraintEvaluation
from core.domain.components.repository import CategorySummary
from core.domain.components.specifications import ComponentSpecification

MAX_PROPERTIES = 100


class ProvenanceModel(ApiModel):
    kind: str = Field(description="documented, user_configured, measured, estimated, inferred or unknown.")
    sources: list[str] = Field(description="Ids of the specification's sources.")
    assumptions: list[str]
    model: str | None
    note: str | None


class SourceModel(ApiModel):
    id: str
    name: str
    reference: str = Field(description="A URL or a document reference.")
    version: str | None
    published: dt.date | None
    retrieved: dt.date | None = Field(description="When the claims were last checked against the source.")


class CapabilityModel(ApiModel):
    id: str
    state: str = Field(
        description="native, requires_configuration, requires_external, unsupported or unknown."
    )
    requires: list[str]
    note: str | None
    provenance: ProvenanceModel


class ConfigurationFieldModel(ApiModel):
    property: str = Field(description="An Architecture IR node property; its unit is part of its name.")
    required: bool
    user_configurable: bool
    engines: list[str]
    default: Any = Field(description="Only when documented; null otherwise (never assumed).")
    description: str | None
    provenance: ProvenanceModel


class CapacityDimensionModel(ApiModel):
    id: str
    unit: str
    scope: str
    value: str | None = Field(
        description="An exact decimal, with its basis; null: read from the configuration."
    )
    property: str | None
    assumptions: list[str]
    description: str | None
    provenance: ProvenanceModel


class ScalingMethodModel(ApiModel):
    id: str
    state: str
    requires: list[str]
    limitations: list[str]
    affects: list[str]
    evaluated_by: list[str] = Field(description="The engines that can evaluate it; empty: none can.")
    provenance: ProvenanceModel


class FailureModeModel(ApiModel):
    id: str
    description: str
    impact: str
    preconditions: list[str]
    signals: list[str]
    mitigations: list[str]
    provenance: ProvenanceModel


class SignalModel(ApiModel):
    id: str
    type: str
    availability: str = Field(
        description="How the technology provides it, not whether a deployment collects it."
    )
    unit: str | None
    collection: str | None
    description: str | None
    provenance: ProvenanceModel


class SecurityPropertyModel(ApiModel):
    id: str
    state: str = Field(description="Whether the technology supports it; the configuration says if it is on.")
    property: str | None
    requires: list[str]
    note: str | None
    provenance: ProvenanceModel


class BillingDimensionModel(ApiModel):
    id: str
    unit: str
    driver: str | None
    description: str | None
    provenance: ProvenanceModel


class OperationalConsiderationModel(ApiModel):
    area: str
    responsibility: str
    statement: str
    provenance: ProvenanceModel


class ConditionModel(ApiModel):
    property: str
    values: list[Any]


class ConstraintModel(ApiModel):
    id: str
    type: str = Field(
        description="hard_limit, configurable_limit, conditional_limit, recommended_range, "
        "unsupported_configuration or unknown."
    )
    description: str
    property: str
    comparison: str | None
    limit: str | None
    minimum: str | None
    maximum: str | None
    values: list[Any]
    severity: str
    conditions: list[ConditionModel]
    technology_versions: list[str]
    remediation: str | None
    provenance: ProvenanceModel


class ProviderModel(ApiModel):
    name: str
    service: str


class ComponentSummary(ApiModel):
    id: str = Field(description="The catalog path a node's component refers to, e.g. databases/postgresql.")
    version: int
    ref: str = Field(description="id@version: what an evaluation records having used.")
    name: str
    category: str
    technology: str
    node_kinds: list[str]
    support_status: str = Field(description="supported, partial, planned or deprecated.")
    provider: ProviderModel
    hosting: str | None
    aliases: list[str]
    replaced_by: str | None

    @classmethod
    def of(cls, spec: ComponentSpecification) -> Self:
        return cls.model_validate(spec.to_dict() | {"ref": spec.ref})


class ComponentSpecificationModel(ComponentSummary):
    description: str
    content_hash: str
    technology_versions: list[str]
    capabilities: list[CapabilityModel]
    configuration: list[ConfigurationFieldModel]
    capacity: list[CapacityDimensionModel]
    scaling: list[ScalingMethodModel]
    failure_modes: list[FailureModeModel]
    signals: list[SignalModel]
    security: list[SecurityPropertyModel]
    billing: list[BillingDimensionModel]
    operations: list[OperationalConsiderationModel]
    constraints: list[ConstraintModel]
    sources: list[SourceModel]
    current: bool = Field(description="Whether this is the component's current version.")

    @classmethod
    def of_version(cls, spec: ComponentSpecification, *, current: bool) -> Self:
        return cls.model_validate(
            spec.to_dict() | {"ref": spec.ref, "content_hash": spec.content_hash, "current": current}
        )


class ComponentList(ApiModel):
    components: list[ComponentSummary]
    catalog_fingerprint: str


class CategoryModel(ApiModel):
    id: str
    name: str
    directory: str
    node_kinds: list[str]
    components: int
    by_status: dict[str, int]

    @classmethod
    def of(cls, summary: CategorySummary) -> Self:
        category = summary.category
        return cls(
            id=category.id,
            name=category.name,
            directory=category.directory,
            node_kinds=sorted(k.value for k in category.node_kinds),
            components=summary.components,
            by_status={s.value: n for s, n in summary.by_status.items()},
        )


class CategoryList(ApiModel):
    categories: list[CategoryModel]
    catalog_fingerprint: str


class VersionModel(ApiModel):
    version: int
    ref: str
    content_hash: str
    support_status: str


class VersionList(ApiModel):
    component: str
    current: int
    versions: list[VersionModel]


class ConfigurationInput(RequestModel):
    values: dict[str, Any] = Field(
        default_factory=dict,
        max_length=MAX_PROPERTIES,
        description="Architecture IR node properties, as an architecture states them (decimals as strings).",
    )
    unknown: list[str] = Field(
        default_factory=list, max_length=MAX_PROPERTIES, description="Properties whose value is not known."
    )


class EvaluateConfigurationRequest(RequestModel):
    version: int | None = Field(
        default=None, ge=1, description="A specification version; default the current."
    )
    node_kind: NodeKind = Field(description="The node kind the configuration belongs to.")
    technology_version: str | None = Field(default=None, max_length=32)
    configuration: ConfigurationInput = Field(default_factory=ConfigurationInput)


class ConstraintFindingModel(ApiModel):
    id: str
    component: str
    specification: str | None
    check: str
    constraint_type: str | None
    outcome: str = Field(description="pass, warning, violation, cannot_evaluate or not_applicable.")
    severity: str | None
    property: str | None
    actual: Any
    expected: str | None
    unit: str | None
    explanation: str
    remediation: str | None
    provenance: ProvenanceModel | None


class ConstraintEvaluationModel(ApiModel):
    specifications: dict[str, str] = Field(
        description="Every specification version used (ref: content hash)."
    )
    catalog_fingerprint: str
    findings: list[ConstraintFindingModel]
    summary: dict[str, int] = Field(description="Counts by outcome; no score.")
    fingerprint: str

    @classmethod
    def of(cls, evaluation: ConstraintEvaluation) -> Self:
        data = evaluation.to_dict()
        keys = ("specifications", "catalog_fingerprint", "findings", "summary", "fingerprint")
        return cls.model_validate({key: data[key] for key in keys})
