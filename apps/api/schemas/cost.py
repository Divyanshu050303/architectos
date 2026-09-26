"""Cost analyses over HTTP: the request (revision, pricing snapshot, currency, pricing date,
operating hours, capacity analysis, scenarios) and the stored analysis, typed field by field.
Amounts are exact decimal strings with their currency and a display value rounded to the currency's
minor unit; an unknown amount is null, never 0."""

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import Field

from core.domain.capacity.units import exact
from core.domain.cost.analyses import MAX_ASSUMPTIONS, CostAssumption
from core.domain.cost.pricing import PricingModel, PricingSource, PricingUnit
from core.domain.cost.projection import MAX_COST_SCENARIOS, CostScenario
from core.domain.cost.reports import CostReport
from core.domain.cost.results import CostCategory, CostKind, LineItem, LineStatus

from .capacity import (
    AnalysisErrorModel,
    EvidenceModel,
    LimitationModel,
    ModelRefModel,
    ModelSetModel,
    Number,
    ScenarioEchoModel,
    ScenarioInput,
    UnsupportedModel,
)
from .common import ApiModel, RequestModel

# --- request -------------------------------------------------------------------------------------


class CostAssumptionInput(RequestModel):
    key: Annotated[str, Field(min_length=1, max_length=64, examples=["reserved_instances"])]
    statement: Annotated[str, Field(min_length=1, max_length=500)]

    def to_domain(self) -> CostAssumption:
        return CostAssumption(self.key, self.statement)


class CostScenarioInput(ScenarioInput):
    """A capacity scenario (growth, growth rate over periods, or target rate, and configuration
    changes), priced; optionally with other operating hours or the capacity-required replicas."""

    operating_hours_per_month: Number | None = None
    replicas_from_capacity: bool | None = None

    def to_cost_domain(self) -> CostScenario:
        hours = self.operating_hours_per_month
        return CostScenario(
            self.to_domain(),
            exact(hours, "operating_hours_per_month") if hours is not None else None,
            self.replicas_from_capacity,
        )


class RunCostAnalysisRequest(RequestModel):
    """The pricing snapshot is required; everything else is optional."""

    revision: Annotated[int | None, Field(ge=1, description="Default: the current revision.")] = None
    snapshot_id: uuid.UUID = Field(description="A pricing snapshot of the project's organization.")
    currency: Annotated[
        str | None, Field(min_length=3, max_length=3, description="Default: the project's currency.")
    ] = None
    pricing_date: date | None = Field(
        default=None, description="Prices effective on this day. Default: today (UTC)."
    )
    operating_hours_per_month: Number | None = Field(
        default=None, description="Hours resources run per month, up to 730 (the default)."
    )
    capacity_analysis_id: uuid.UUID | None = Field(
        default=None, description="A capacity analysis of the same revision, for usage-priced resources."
    )
    replicas_from_capacity: bool = Field(
        default=False, description="Bill the replicas the capacity analysis requires, where it says."
    )
    assumptions: Annotated[list[CostAssumptionInput], Field(max_length=MAX_ASSUMPTIONS)] = Field(
        default_factory=list
    )
    label: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    scenarios: Annotated[list[CostScenarioInput], Field(max_length=MAX_COST_SCENARIOS)] = Field(
        default_factory=list
    )


# --- responses -----------------------------------------------------------------------------------


class MoneyModel(ApiModel):
    amount: str = Field(description='Exact decimal (up to 12 places), e.g. "189.8".')
    currency: str
    display: str = Field(description='Rounded half-even to the currency\'s minor unit, e.g. "189.80".')


class PeriodsModel(ApiModel):
    """One amount per billing period, derived from the monthly one (730 h/month, 24 h/day, 8760 h/year)."""

    hour: MoneyModel
    day: MoneyModel
    month: MoneyModel
    year: MoneyModel


class TotalsModel(ApiModel):
    currency: str
    hourly: MoneyModel
    monthly: MoneyModel
    annual: MoneyModel
    unknown_items: int
    unsupported: int
    complete: bool = Field(
        description="False when any item is unknown or unsupported: the amounts are a lower bound."
    )


class ShareModel(ApiModel):
    key: str | None = Field(
        description="null groups the lines without a value (e.g. no price, so no provider)."
    )
    hourly: MoneyModel
    monthly: MoneyModel
    annual: MoneyModel
    share: str | None = Field(description="Of the known monthly total; null when that total is 0.")
    priced_items: int
    unknown_items: int
    complete: bool


class UnknownItemModel(ApiModel):
    element_id: str
    resource: str
    category: CostCategory
    kind: CostKind
    missing: list[str]
    reason: str | None


class DriverItemModel(ApiModel):
    element_id: str
    resource: str
    category: CostCategory
    kind: CostKind
    sensitivity: str = Field(description="fixed, stepwise, linear or tiered: how it responds to workload.")
    monthly: MoneyModel
    annual: MoneyModel
    share: str | None


class DriversModel(ApiModel):
    largest_component: ShareModel | None
    largest_category: ShareModel | None
    top_items: list[DriverItemModel]
    fixed: MoneyModel
    usage: MoneyModel
    workload_sensitive: MoneyModel


class CostSummaryModel(ApiModel):
    status: str
    totals: TotalsModel
    known_total_is_lower_bound: bool
    by_component: list[ShareModel]
    by_resource: list[ShareModel]
    by_category: list[ShareModel]
    by_provider: list[ShareModel]
    by_region: list[ShareModel]
    by_kind: list[ShareModel]
    by_sensitivity: list[ShareModel]
    unknown: list[UnknownItemModel]
    drivers: DriversModel


class LineDeltaModel(ApiModel):
    element_id: str
    resource: str
    baseline_quantity: str | None
    scenario_quantity: str | None
    baseline_monthly: MoneyModel | None
    scenario_monthly: MoneyModel | None
    difference: MoneyModel | None = Field(description="null when either side is unknown.")
    baseline_status: LineStatus | None
    scenario_status: LineStatus | None
    sensitivity: str | None


class CostComparisonModel(ApiModel):
    baseline: PeriodsModel
    scenario: PeriodsModel
    baseline_complete: bool
    scenario_complete: bool
    complete: bool
    difference: PeriodsModel = Field(description="Of the known totals: a true difference only when complete.")
    percentage: str | None
    percentage_undefined: str | None = Field(
        description="incomplete or zero_baseline when percentage is null."
    )
    changed_lines: list[LineDeltaModel]
    changed_assumptions: list[EvidenceModel]
    unknown_differences: list[str] = Field(
        description='"component/resource" of every line unknown on either side.'
    )


class CostScenarioEchoModel(ApiModel):
    scenario: ScenarioEchoModel
    operating_hours_per_month: str | None
    replicas_from_capacity: bool | None


class ScenarioProjectionModel(ApiModel):
    name: str
    scenario: CostScenarioEchoModel
    status: str
    periods: PeriodsModel
    comparison: CostComparisonModel
    assumptions: list[EvidenceModel]
    result_fingerprint: str


class PriceRefModel(ApiModel):
    snapshot_id: uuid.UUID
    record_id: str
    provider: str
    service: str
    sku: str
    region: str
    currency: str
    unit: PricingUnit
    model: PricingModel
    effective_from: date
    effective_to: date | None
    source: PricingSource
    retrieved_at: datetime | None


class LineItemModel(ApiModel):
    element_id: str
    resource: str
    category: CostCategory
    kind: CostKind
    status: LineStatus
    quantity: str | None = Field(description="Per month, in unit.")
    unit: PricingUnit | None
    unit_price: str | None
    per: str | None
    monthly: MoneyModel | None = Field(description="null when unknown (never 0 instead).")
    price: PriceRefModel | None
    model_id: str | None
    model_version: int | None
    assumptions: list[EvidenceModel]
    missing: list[str]
    reason: str | None

    @classmethod
    def of(cls, line: LineItem) -> LineItemModel:
        return cls.model_validate(line.to_dict())


class LineItemPage(ApiModel):
    line_items: list[LineItemModel]
    next_cursor: str | None


class CostAnalysisSummary(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    revision: int
    revision_content_hash: str
    label: str | None
    status: str = Field(description="completed, partial, insufficient_pricing, unsupported or failed.")
    snapshot_id: uuid.UUID
    currency: str
    requested_by_user_id: uuid.UUID | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result_fingerprint: str | None
    totals: TotalsModel | None
    error: AnalysisErrorModel | None

    @classmethod
    def fields_for(cls, report: CostReport) -> dict[str, Any]:
        a = report.analysis
        return {
            "id": a.id,
            "project_id": a.project_id,
            "architecture_id": a.architecture_id,
            "revision": a.revision_number,
            "revision_content_hash": a.revision_content_hash,
            "label": a.label,
            "status": a.status,
            "snapshot_id": report.snapshot_id,
            "currency": report.currency,
            "requested_by_user_id": a.requested_by_user_id,
            "requested_at": a.requested_at,
            "started_at": a.started_at,
            "completed_at": a.completed_at,
            "result_fingerprint": report.result_fingerprint,
            "totals": TotalsModel.model_validate(report.totals.to_dict()) if report.totals else None,
            "error": AnalysisErrorModel(code=a.error.code, message=a.error.message) if a.error else None,
        }

    @classmethod
    def of(cls, report: CostReport) -> CostAnalysisSummary:
        return cls(**cls.fields_for(report))


class CostAnalysisResponse(CostAnalysisSummary):
    """The analysis without its line items (GET …/line-items)."""

    inputs: dict[str, Any] = Field(
        description="The request as stored: snapshot, currency, pricing date, operating hours, capacity "
        "analysis, assumptions, provider, scenarios."
    )
    snapshot_hash: str | None
    model_set: ModelSetModel | None
    context_fingerprint: str | None
    summary: CostSummaryModel | None
    unsupported: list[UnsupportedModel]
    limitations: list[LimitationModel]
    assumptions: list[EvidenceModel] = Field(
        description="What the projection rests on (conventions, hours, …)."
    )
    scenarios: list[ScenarioProjectionModel]

    @classmethod
    def of(cls, report: CostReport) -> CostAnalysisResponse:
        model_set = report.model_set
        return cls(
            **cls.fields_for(report),
            inputs=dict(report.inputs),
            snapshot_hash=report.snapshot_hash,
            model_set=ModelSetModel(
                version=model_set.version,
                models=[ModelRefModel(id=m[0], version=m[1]) for m in model_set.models],
            )
            if model_set
            else None,
            context_fingerprint=report.context_fingerprint,
            summary=CostSummaryModel.model_validate(report.summary) if report.summary is not None else None,
            unsupported=[UnsupportedModel.model_validate(u.to_dict()) for u in report.unsupported],
            limitations=[LimitationModel.model_validate(x.to_dict()) for x in report.limitations],
            assumptions=[EvidenceModel.model_validate(e.to_dict()) for e in report.assumptions],
            scenarios=[ScenarioProjectionModel.model_validate(s) for s in report.scenarios],
        )


class CostAnalysisPage(ApiModel):
    analyses: list[CostAnalysisSummary]
    next_cursor: str | None


class CostModelModel(ApiModel):
    id: str
    version: int
    name: str
    description: str
    kinds: list[str] = Field(description="The node kinds it prices.")
    resources: list[str] = Field(description="The line resources it bills.")
    units: list[str] = Field(description="The pricing units its charges use.")
    configuration: list[str] = Field(description="Configuration properties it reads.")
    usage: list[str] = Field(description="Usage inputs it needs from a capacity analysis.")
    limitations: list[str]


class CostModelCatalog(ApiModel):
    models: list[CostModelModel]
