"""Simulations over HTTP: the request (a scenario and the inputs its engines need) and the stored
simulation, typed field by field. Results are model-based projections of the architecture as
declared: they do not guarantee real-world performance, availability, cost or failure behavior.
Numbers are exact decimal strings; an unknown value is null, never 0."""

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import Field, StrictBool, StrictInt

from core.domain.engine_results import ModelSet
from core.domain.simulations.entities import (
    MAX_ASSUMPTIONS,
    MAX_ENTRIES,
    PricingInputs,
    SimulationAssumption,
)
from core.domain.simulations.reports import SimulationReport
from core.domain.simulations.scenarios import MAX_CHANGES, MAX_FAILURES, Scenario
from core.domain.simulations.values import AnalysisKind, FailureKind, Impact, RunState

from .capacity import (
    AnalysisErrorModel,
    EvidenceModel,
    LimitationModel,
    ModelRefModel,
    ModelSetModel,
    QuantityInput,
    UnsupportedModel,
    WorkloadInput,
)
from .common import ApiModel, RequestModel

type Number = Annotated[str, Field(max_length=40)] | StrictInt | float
type Text = Annotated[str, Field(max_length=200)]
type Value = StrictBool | StrictInt | Text | list[Text]
type ElementId = Annotated[str, Field(min_length=1, max_length=128)]

# --- request -------------------------------------------------------------------------------------


class WorkloadChangeInput(RequestModel):
    growth: Number | None = Field(default=None, description="A multiplier of the workload's rates.")
    growth_rate: Number | None = Field(default=None, description="Compound growth per period, with periods.")
    periods: StrictInt | None = None
    target_rate: QuantityInput | None = Field(default=None, description="A rate with its unit.")
    target_utilization: Number | None = None


class ChangeInput(RequestModel):
    element_id: ElementId
    property: Annotated[str, Field(min_length=1, max_length=64, examples=["replicas"])]
    value: Value | None = Field(description="The property's type (units in its name); null clears it.")


class FailureInput(RequestModel):
    kind: FailureKind
    target: Annotated[str, Field(min_length=1, max_length=128, examples=["db", "eu-west-1a"])]


class ScenarioInput(RequestModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str | None, Field(min_length=1, max_length=500)] = None
    workload: WorkloadChangeInput | None = None
    changes: Annotated[list[ChangeInput], Field(max_length=MAX_CHANGES)] = Field(default_factory=list)
    failures: Annotated[list[FailureInput], Field(max_length=MAX_FAILURES)] = Field(default_factory=list)

    def to_domain(self) -> Scenario:
        """Validated by the domain's own scenario rules (units, ranges, conflicts)."""
        return Scenario.from_dict(
            {
                "name": self.name,
                "description": self.description,
                "workload": self.workload.model_dump(by_alias=False) if self.workload is not None else None,
                "changes": [c.model_dump(by_alias=False) for c in self.changes],
                "failures": [{"kind": f.kind.value, "target": f.target} for f in self.failures],
            }
        )


class PricingInput(RequestModel):
    snapshot_id: uuid.UUID = Field(description="An organization pricing snapshot: the same for both sides.")
    pricing_date: date | None = Field(default=None, description="Default: today.")
    operating_hours_per_month: Number | None = Field(default=None, description="Default: 730.")

    def to_domain(self, today: date) -> PricingInputs:
        if self.operating_hours_per_month is None:
            return PricingInputs(self.snapshot_id, self.pricing_date or today)
        # Passed as given: the Cost Engine's request rules validate it (invalid: a 422, not a crash).
        hours: Any = self.operating_hours_per_month
        return PricingInputs(self.snapshot_id, self.pricing_date or today, hours)


class SimulationAssumptionInput(RequestModel):
    key: Annotated[str, Field(min_length=1, max_length=64, examples=["peak"])]
    statement: Annotated[str, Field(min_length=1, max_length=500)]

    def to_domain(self) -> SimulationAssumption:
        return SimulationAssumption(self.key, self.statement)


class RunSimulationRequest(RequestModel):
    revision: Annotated[int | None, Field(ge=1, description="Default: the current revision.")] = None
    scenario: ScenarioInput
    analyses: Annotated[list[AnalysisKind] | None, Field(max_length=3)] = Field(
        default=None, description="Default: every analysis the scenario concerns."
    )
    workload: WorkloadInput | None = Field(default=None, description="Needed by capacity (and cost usage).")
    entries: Annotated[list[ElementId] | None, Field(max_length=MAX_ENTRIES)] = Field(
        default=None, description="Where requests enter; default: the clients."
    )
    pricing: PricingInput | None = Field(default=None, description="Needed by cost.")
    assumptions: Annotated[list[SimulationAssumptionInput], Field(max_length=MAX_ASSUMPTIONS)] = Field(
        default_factory=list, description="Recorded with the simulation, never computed with."
    )
    label: Annotated[str | None, Field(min_length=1, max_length=100)] = None


# --- responses -----------------------------------------------------------------------------------


class DeltaModel(ApiModel):
    analysis: AnalysisKind
    element_id: str = Field(description="A node, a connection, or 'system'.")
    metric: str
    unit: str
    baseline: str | None = Field(description="null: not known (never 0).")
    scenario: str | None
    difference: str | None = Field(description="Only when both are known and comparable.")
    percentage: str | None = Field(description="Only when comparable and the baseline is not 0.")
    comparable: bool
    note: str | None = Field(description="Why it is not comparable (e.g. incomplete, unknown_line).")


class DeltaPage(ApiModel):
    deltas: list[DeltaModel]
    next_cursor: str | None


class ComponentOutcomeModel(ApiModel):
    node_id: str
    unavailable: bool
    changes: list[EvidenceModel] = Field(description="What the scenario changed on it: 'before -> after'.")
    impact: Impact | None = Field(description="Its failure impact; null when no failure concerns it.")


class ComponentOutcomePage(ApiModel):
    components: list[ComponentOutcomeModel]
    next_cursor: str | None


class EntryImpactModel(ApiModel):
    entry_id: str
    impact: Impact
    explanation: str
    through: list[str] = Field(description="The unavailable elements it requires.")
    missing: list[str] = Field(description="What would decide an unknown impact.")


class AnalysisRunModel(ApiModel):
    analysis: AnalysisKind
    state: RunState
    model_set: ModelSetModel | None
    baseline_fingerprint: str | None = Field(description="The engine's own baseline result.")
    scenario_fingerprint: str | None
    reason: str | None = Field(description="Why it did not run (no_workload, no_pricing, not_concerned…).")
    message: str | None


class SimulationSummaryModel(ApiModel):
    """Counts only; no score."""

    runs: dict[str, int]
    entries: dict[str, int] = Field(description="Entry points by impact.")
    components: int
    unavailable: int
    deltas: dict[str, int] = Field(description="comparable, not_comparable, changed.")
    unsupported: int


def model_set(value: ModelSet | None) -> ModelSetModel | None:
    if value is None:
        return None
    return ModelSetModel(
        version=value.version, models=[ModelRefModel(id=m[0], version=m[1]) for m in value.models]
    )


class SimulationSummary(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    revision: int
    revision_content_hash: str
    label: str | None
    status: str = Field(description="completed, partial, unsupported or failed.")
    requested_by_user_id: uuid.UUID | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    scenario_fingerprint: str | None
    result_fingerprint: str | None
    summary: SimulationSummaryModel | None
    error: AnalysisErrorModel | None

    @classmethod
    def fields_for(cls, report: SimulationReport) -> dict[str, Any]:
        s = report.simulation
        return {
            "id": s.id,
            "project_id": s.project_id,
            "architecture_id": s.architecture_id,
            "revision": s.revision_number,
            "revision_content_hash": s.revision_content_hash,
            "label": s.label,
            "status": s.status,
            "requested_by_user_id": s.requested_by_user_id,
            "requested_at": s.requested_at,
            "started_at": s.started_at,
            "completed_at": s.completed_at,
            "scenario_fingerprint": report.scenario_fingerprint,
            "result_fingerprint": report.result_fingerprint,
            "summary": SimulationSummaryModel.model_validate(report.summary)
            if report.summary is not None
            else None,
            "error": AnalysisErrorModel(code=s.error.code, message=s.error.message) if s.error else None,
        }

    @classmethod
    def of(cls, report: SimulationReport) -> SimulationSummary:
        return cls(**cls.fields_for(report))


class SimulationResponse(SimulationSummary):
    """The simulation without its component outcomes and deltas (GET …/components, …/deltas)."""

    inputs: dict[str, Any] = Field(
        description="The request as stored (with the scenario snapshot), the requirements read, the "
        "provider and the currency."
    )
    overlay: dict[str, Any] | None = Field(
        description="What the scenario changed, as evaluated (never the architecture itself)."
    )
    engine_set: ModelSetModel | None
    context_fingerprint: str | None
    runs: list[AnalysisRunModel]
    entries: list[EntryImpactModel]
    assumptions: list[EvidenceModel]
    trace: list[EvidenceModel] = Field(
        description="How the scenario was applied and evaluated, step by step."
    )
    unsupported: list[UnsupportedModel]
    limitations: list[LimitationModel]

    @classmethod
    def of(cls, report: SimulationReport) -> SimulationResponse:
        return cls(
            **cls.fields_for(report),
            inputs=dict(report.inputs),
            overlay=dict(report.overlay) if report.overlay is not None else None,
            engine_set=model_set(report.engine_set),
            context_fingerprint=report.context_fingerprint,
            runs=[
                AnalysisRunModel(
                    analysis=r.analysis,
                    state=r.state,
                    model_set=model_set(r.model_set),
                    baseline_fingerprint=r.baseline_fingerprint,
                    scenario_fingerprint=r.scenario_fingerprint,
                    reason=r.reason,
                    message=r.message,
                )
                for r in report.runs
            ],
            entries=[EntryImpactModel.model_validate(e.to_dict()) for e in report.entries],
            assumptions=[EvidenceModel.model_validate(e.to_dict()) for e in report.assumptions],
            trace=[EvidenceModel.model_validate(e.to_dict()) for e in report.trace],
            unsupported=[UnsupportedModel.model_validate(u.to_dict()) for u in report.unsupported],
            limitations=[LimitationModel.model_validate(x.to_dict()) for x in report.limitations],
        )


class SimulationPage(ApiModel):
    simulations: list[SimulationSummary]
    next_cursor: str | None


class AnalysisComparisonModel(ApiModel):
    analysis: AnalysisKind
    comparable: bool
    reason: str | None = Field(
        description="not_run, different_engine, different_models or different_baseline."
    )
    model_set: str | None
    baseline_fingerprint: str | None


class SimulationComparisonResponse(ApiModel):
    first: str = Field(
        description="The first simulation's result fingerprint (its scenario is the baseline side)."
    )
    second: str
    comparable: bool
    analyses: list[AnalysisComparisonModel]
    deltas: list[DeltaModel]


class ScenarioTypeModel(ApiModel):
    id: str
    version: int
    name: str
    description: str
    applies_to: str
    inputs: list[str]
    properties: list[str]
    analyses: list[AnalysisKind]
    requires: list[str]
    overlay: str
    unsupported: list[str]
    limit: int
    output: str


class EvaluatorModel(ApiModel):
    analysis: AnalysisKind
    version: int
    name: str
    description: str
    requires: list[str]
    unsupported: list[str]


class LimitsModel(ApiModel):
    max_changes: int
    max_failures: int
    max_affected_components: int
    max_deltas: int


class SimulationCatalog(ApiModel):
    scenario_types: list[ScenarioTypeModel]
    evaluators: list[EvaluatorModel]
    limits: LimitsModel
