"""Evolution analyses and decision records over HTTP, typed field by field. Candidates are proposals
for engineering review: nothing here applies a change to the architecture, and nothing ranks the
candidates or chooses between them. Numbers are exact decimal strings; an unknown value is null."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, StrictInt

from core.domain.capacity.errors import InvalidQuantity
from core.domain.decisions.entities import Decision, DecisionStatus
from core.domain.engine_results import Evidence, ModelSet
from core.domain.evolution.candidates import Candidate
from core.domain.evolution.entities import (
    MAX_ASSUMPTIONS,
    MAX_FROZEN,
    MAX_REQUIREMENTS,
    MAX_SCOPE,
    EvidenceCitation,
    EvolutionConstraints,
)
from core.domain.evolution.errors import InvalidEvolutionRequest
from core.domain.evolution.goals import EvolutionGoal
from core.domain.evolution.reports import EvolutionReport
from core.domain.evolution.results import MAX_GOALS
from core.domain.evolution.tradeoffs import Alternatives
from core.domain.evolution.values import (
    Basis,
    CandidateCategory,
    Direction,
    EvidenceSource,
    EvidenceState,
    GoalType,
    ValidationState,
)
from core.domain.observability.values import Dimension as Coverage
from core.domain.requirements.enums import RequirementPriority

from .capacity import (
    AnalysisErrorModel,
    EvidenceModel,
    LimitationModel,
    ModelRefModel,
    ModelSetModel,
    QuantityInput,
)
from .common import ApiModel, RequestModel

type ElementId = Annotated[str, Field(min_length=1, max_length=128)]

# --- request ---------------------------------------------------------------------------------------


class FindingRefInput(RequestModel):
    source: EvidenceSource = Field(description="reliability, security, observability or validation.")
    finding_id: Annotated[str, Field(min_length=1, max_length=128)]


class GoalInput(RequestModel):
    type: GoalType
    target: QuantityInput | None = Field(
        default=None,
        description="increase_workload: a rate; recovery_objective: a duration; availability: a ratio or %.",
    )
    amount: Annotated[str, Field(max_length=40)] | StrictInt | None = Field(
        default=None, description="cost_ceiling: the monthly amount."
    )
    currency: Annotated[str | None, Field(max_length=3)] = None
    finding: FindingRefInput | None = None
    dimension: Coverage | None = None
    requirement_id: uuid.UUID | None = None
    priority: RequirementPriority | None = None

    def to_domain(self) -> EvolutionGoal:
        """Validated by the domain's goal rules (every field a type does not use must be absent)."""
        try:
            return EvolutionGoal.from_dict(
                {
                    "type": self.type.value,
                    "target": self.target.to_domain("goals.target").to_dict() if self.target else None,
                    "amount": str(self.amount) if self.amount is not None else None,
                    "currency": self.currency,
                    "finding": {"source": self.finding.source.value, "finding_id": self.finding.finding_id}
                    if self.finding
                    else None,
                    "dimension": self.dimension.value if self.dimension else None,
                    "requirement_id": str(self.requirement_id) if self.requirement_id else None,
                    "priority": self.priority.value if self.priority else None,
                }
            )
        except InvalidQuantity as error:
            raise InvalidEvolutionRequest(
                details={
                    "field": "goals.target",
                    "reason": str(error.details.get("reason") or "invalid_unit"),
                }
            ) from None


class ConstraintsInput(RequestModel):
    frozen_elements: Annotated[list[ElementId], Field(max_length=MAX_FROZEN)] = Field(default_factory=list)
    excluded_categories: list[CandidateCategory] = Field(default_factory=list)
    max_replicas: Annotated[int | None, Field(ge=1, le=1000)] = None

    def to_domain(self) -> EvolutionConstraints:
        return EvolutionConstraints(
            tuple(self.frozen_elements), tuple(self.excluded_categories), self.max_replicas
        )


class CitationInput(RequestModel):
    source: EvidenceSource
    analysis_id: uuid.UUID

    def to_domain(self) -> EvidenceCitation:
        return EvidenceCitation(self.source, self.analysis_id)


class AssumptionInput(RequestModel):
    key: Annotated[str, Field(min_length=1, max_length=64, examples=["peak"])]
    statement: Annotated[str, Field(min_length=1, max_length=500)]


class RunEvolutionRequest(RequestModel):
    revision: Annotated[int | None, Field(ge=1, description="Default: the current revision.")] = None
    goals: Annotated[list[GoalInput], Field(min_length=1, max_length=MAX_GOALS)]
    requirement_ids: Annotated[list[uuid.UUID] | None, Field(max_length=MAX_REQUIREMENTS)] = Field(
        default=None, description="Default: every in-force requirement."
    )
    constraints: ConstraintsInput | None = None
    scope: Annotated[list[ElementId] | None, Field(max_length=MAX_SCOPE)] = Field(
        default=None, description="The nodes to consider; default all."
    )
    evidence: Annotated[list[CitationInput], Field(max_length=6)] = Field(
        default_factory=list, description="Stored analyses to use; default the latest of each engine."
    )
    assumptions: Annotated[list[AssumptionInput], Field(max_length=MAX_ASSUMPTIONS)] = Field(
        default_factory=list, description="Recorded with the analysis, never computed with."
    )
    label: Annotated[str | None, Field(min_length=1, max_length=100)] = None

    def assumption_values(self) -> tuple[Evidence, ...]:
        return tuple(Evidence(a.key, a.statement) for a in self.assumptions)


# --- responses: analyses ----------------------------------------------------------------------------


def model_set(value: ModelSet | None) -> ModelSetModel | None:
    if value is None:
        return None
    return ModelSetModel(
        version=value.version, models=[ModelRefModel(id=m[0], version=m[1]) for m in value.models]
    )


class EvidenceRefModel(ApiModel):
    source: EvidenceSource
    reference: str = Field(description="The stored analysis id (or requirement id, or goal key).")
    state: EvidenceState = Field(description="current: of the baseline's exact content; stale: never used.")
    item: str | None = Field(description="The finding id or scaling option within it.")
    revision_number: int | None
    content_hash: str | None
    model_version: str | None


class FindingModel(ApiModel):
    id: str
    type: str
    message: str
    goal: str | None
    element_ids: list[str]
    evidence: list[EvidenceRefModel]
    missing: list[str] = Field(description="What would let the engine decide.")


class EvolutionAnalysisSummary(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    revision: int
    revision_content_hash: str
    label: str | None
    status: str = Field(description="completed, partial, insufficient_evidence or failed.")
    requested_by_user_id: uuid.UUID | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result_fingerprint: str | None
    summary: dict[str, Any] | None = Field(description="Counts only; no score.")
    error: AnalysisErrorModel | None

    @classmethod
    def fields_for(cls, report: EvolutionReport) -> dict[str, Any]:
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
            "summary": dict(report.summary) if report.summary is not None else None,
            "error": AnalysisErrorModel(code=a.error.code, message=a.error.message) if a.error else None,
        }

    @classmethod
    def of(cls, report: EvolutionReport) -> EvolutionAnalysisSummary:
        return cls(**cls.fields_for(report))


class EvolutionAnalysisResponse(EvolutionAnalysisSummary):
    """The analysis without its candidates (GET …/candidates)."""

    inputs: dict[str, Any] = Field(
        description="The request as stored, the requirements read, policy, provider."
    )
    model_set: ModelSetModel | None = Field(description="The evolution engine and each rule, with versions.")
    goals: list[dict[str, Any]] = Field(description="Each goal: key, type, target, method (the engine).")
    findings: list[FindingModel]
    evidence: list[EvidenceRefModel]
    assumptions: list[EvidenceModel]
    limitations: list[LimitationModel]
    unsupported_goals: list[str]

    @classmethod
    def of(cls, report: EvolutionReport) -> EvolutionAnalysisResponse:
        return cls(
            **cls.fields_for(report),
            inputs=dict(report.inputs),
            model_set=model_set(report.model_set),
            goals=[g.to_dict() for g in report.goals],
            findings=[FindingModel.model_validate(f.to_dict()) for f in report.findings],
            evidence=[EvidenceRefModel.model_validate(e.to_dict()) for e in report.evidence],
            assumptions=[EvidenceModel.model_validate(e.to_dict()) for e in report.assumptions],
            limitations=[LimitationModel.model_validate(x.to_dict()) for x in report.limitations],
            unsupported_goals=sorted(
                {
                    f.goal
                    for f in report.findings
                    if f.goal and f.type.value in ("goal_unsupported", "goal_not_evaluable")
                }
            ),
        )


class EvolutionAnalysisPage(ApiModel):
    analyses: list[EvolutionAnalysisSummary]
    next_cursor: str | None


# --- responses: candidates --------------------------------------------------------------------------


class ChangeModel(ApiModel):
    element_id: str
    property: str
    value: Any = Field(description="The proposed value (null: cleared).")


class EffectModel(ApiModel):
    dimension: str
    code: str
    statement: str
    basis: Basis = Field(description="modeled, rule, or consideration (for human review).")
    evidence: list[EvidenceModel]


class ValidationNoteModel(ApiModel):
    code: str
    message: str
    reference: str | None
    element_ids: list[str]
    blocking: bool


class ImpactDeltaModel(ApiModel):
    element_id: str
    metric: str
    unit: str
    baseline: str | None = Field(description="null: not known (never 0).")
    candidate: str | None
    difference: str | None
    percentage: str | None
    comparable: bool
    note: str | None


class ImpactChangeModel(ApiModel):
    kind: str = Field(description="resolved or introduced.")
    reference: str
    code: str
    element_ids: list[str]
    severity: str | None


class ImpactModel(ApiModel):
    dimension: EvidenceSource
    state: str
    source: EvidenceSource = Field(description="The engine that evaluated it.")
    reason: str | None
    baseline_fingerprint: str | None
    candidate_fingerprint: str | None
    model_version: str | None
    inputs: list[EvidenceRefModel] = Field(description="The stored analyses whose inputs were reused.")
    deltas: list[ImpactDeltaModel]
    changes: list[ImpactChangeModel]
    assumptions: list[EvidenceModel]
    missing: list[str]


class ConsequenceModel(ApiModel):
    dimension: str
    direction: Direction
    basis: Basis
    statement: str
    evidence: list[EvidenceModel]


class RuleRefModel(ApiModel):
    id: str
    version: int


class BaselineModel(ApiModel):
    architecture_id: uuid.UUID
    revision_number: int
    content_hash: str


class CandidateModel(ApiModel):
    id: str = Field(description="Stable: derived from the rule, the baseline and the changes.")
    status: str = Field(description="Always proposed: the engine never applies a candidate.")
    rule: RuleRefModel
    baseline: BaselineModel
    category: CandidateCategory
    title: str
    description: str
    changes: list[ChangeModel]
    goals: list[str]
    rationale: str
    evidence: list[EvidenceRefModel]
    benefits: list[EffectModel]
    tradeoffs: list[EffectModel]
    complexity: list[EffectModel]
    migration: list[EffectModel]
    risks: list[EffectModel]
    missing: list[str]
    validation: ValidationState = Field(description="valid: under the modeled constraints only.")
    validation_notes: list[ValidationNoteModel]
    impacts: list[ImpactModel]
    consequences: list[ConsequenceModel]

    @classmethod
    def of(cls, candidate: Candidate) -> CandidateModel:
        return cls.model_validate(candidate.to_dict())


class CandidatePage(ApiModel):
    candidates: list[CandidateModel]
    next_cursor: str | None


class CandidateDetail(ApiModel):
    candidate: CandidateModel
    overlay: dict[str, Any] | None = Field(
        description="The proposed diff against the exact baseline (never applied); null when it cannot "
        "be applied."
    )


class AlternativeRowModel(ApiModel):
    candidate_id: str
    dimension: str
    direction: Direction


class AlternativesModel(ApiModel):
    goal: str
    candidates: list[str] = Field(description="Canonical order (category, id): not a ranking.")
    table: list[AlternativeRowModel]

    @classmethod
    def of(cls, value: Alternatives) -> AlternativesModel:
        return cls.model_validate(value.to_dict())


class AlternativesResponse(ApiModel):
    alternatives: list[AlternativesModel]


class EvolutionCatalog(ApiModel):
    goal_types: list[str]
    categories: list[str]
    rules: list[dict[str, Any]]
    limits: dict[str, int]


# --- decisions --------------------------------------------------------------------------------------


class DraftDecisionRequest(RequestModel):
    architecture_id: uuid.UUID
    analysis_id: uuid.UUID
    candidate_ids: Annotated[
        list[Annotated[str, Field(pattern=r"^evo_[0-9a-f]{20}$")]] | None, Field(max_length=50)
    ] = Field(default=None, description="Default: every candidate of the analysis.")
    title: Annotated[str | None, Field(min_length=1, max_length=200)] = None


class AcceptDecisionRequest(RequestModel):
    candidate_id: Annotated[str, Field(pattern=r"^evo_[0-9a-f]{20}$")]
    rationale: Annotated[str, Field(min_length=1, max_length=10_000)]


class RejectDecisionRequest(RequestModel):
    rationale: Annotated[str, Field(min_length=1, max_length=10_000)]


class SupersedeDecisionRequest(RequestModel):
    by_decision_id: uuid.UUID


class LinkRevisionRequest(RequestModel):
    revision: Annotated[int, Field(ge=1, description="A revision of the decision's architecture.")]


class DecisionOptionModel(ApiModel):
    candidate_id: str
    title: str
    category: str
    changes: list[str]
    validation: str
    goals: list[str]
    consequences: list[dict[str, str]]


class DecisionSourceModel(ApiModel):
    analysis_id: uuid.UUID
    baseline: BaselineModel
    model_version: str


class ResultingRevisionModel(ApiModel):
    number: int
    content_hash: str
    linked_by_user_id: uuid.UUID
    linked_at: datetime


class DecisionResponse(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    number: int
    reference: str = Field(examples=["ADR-3"])
    title: str
    status: DecisionStatus
    context: str
    options: list[DecisionOptionModel]
    source: DecisionSourceModel | None
    goals: list[str]
    evidence: list[EvidenceRefModel]
    assumptions: list[EvidenceModel]
    related_element_ids: list[str]
    chosen_option: str | None
    rationale: str | None
    decided_by_user_id: uuid.UUID | None
    decided_at: datetime | None
    superseded_by: uuid.UUID | None
    resulting_revision: ResultingRevisionModel | None = Field(
        description="Linked by a person after a separate, authorized architecture change."
    )
    created_by_user_id: uuid.UUID | None
    created_at: datetime

    @classmethod
    def of(cls, decision: Decision) -> DecisionResponse:
        linked = decision.resulting_revision
        return cls(
            id=decision.id,
            project_id=decision.project_id,
            architecture_id=decision.architecture_id,
            number=decision.number,
            reference=decision.reference,
            title=decision.title,
            status=decision.status,
            context=decision.context,
            options=[DecisionOptionModel.model_validate(o.to_dict()) for o in decision.options],
            source=DecisionSourceModel.model_validate(decision.source.to_dict()) if decision.source else None,
            goals=list(decision.goals),
            evidence=[EvidenceRefModel.model_validate(e.to_dict()) for e in decision.evidence],
            assumptions=[EvidenceModel.model_validate(a.to_dict()) for a in decision.assumptions],
            related_element_ids=list(decision.related_element_ids),
            chosen_option=decision.chosen_option,
            rationale=decision.rationale,
            decided_by_user_id=decision.decided_by_user_id,
            decided_at=decision.decided_at,
            superseded_by=decision.superseded_by,
            resulting_revision=ResultingRevisionModel(
                number=linked.number,
                content_hash=linked.content_hash,
                linked_by_user_id=linked.linked_by_user_id,
                linked_at=linked.linked_at,
            )
            if linked
            else None,
            created_by_user_id=decision.created_by_user_id,
            created_at=decision.created_at,
        )


class DecisionPage(ApiModel):
    decisions: list[DecisionResponse]
    next_cursor: str | None


class DecisionDocument(ApiModel):
    reference: str
    markdown: str = Field(description="The decision as an ADR document.")
