import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import Field
from pydantic.alias_generators import to_camel

from core.domain.requirements.analysis import (
    Concern,
    ProjectAnalysis,
    Severity,
    ValidationReport,
)
from core.domain.requirements.entities import Requirement, RequirementChanges, RequirementVersion
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementScope,
    RequirementSource,
    RequirementStatus,
    RequirementType,
)
from core.domain.requirements.normalization import canonical_data
from core.domain.requirements.value_objects import KEEP

from .common import ApiModel, RequestModel

STRUCTURED_DATA_DESCRIPTION = (
    "One structured constraint, or {} for none. Quantity: {metric, operator (>=, >, <=, <, ==), value, "
    'unit, percentile?}, e.g. {"metric": "latency", "operator": "<=", "value": "300", '
    '"unit": "ms", "percentile": "95"}. Range (inclusive): {metric, operator: "between", min, max, '
    'unit, percentile?}. Set: {metric, operator: "in", values: [...]}. '
    "Numbers are exact decimals: send them as strings to avoid binary floating point; they are "
    "always returned as strings. Convenient input forms are normalized deterministically: "
    '{"quantity": "2k requests/sec"} instead of value and unit, value "2k" or "10M", '
    'unit aliases such as rps, seconds or percent, percentile "p95". Ambiguous spellings '
    "(m, gb, $) are refused."
)


class RequirementResponse(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    reference: str = Field(examples=["REQ-12"], description="Stable, human-readable, unique in the project.")
    number: int
    version: int = Field(description="Current version; send it back as expectedVersion when updating.")
    type: RequirementType
    category: str
    scope: RequirementScope = Field(description="What it applies to; system also when unspecified.")
    title: str
    statement: str
    priority: RequirementPriority
    status: RequirementStatus
    source: RequirementSource
    confidence: Decimal | None = Field(
        description="Confidence in the interpretation (AI-sourced only), 0-1. Not a probability "
        "that the requirement is true, and unrelated to priority."
    )
    structured_data: dict[str, Any]
    normalized_data: dict[str, Any] | None = Field(
        description="Derived, not stored: the constraint in its canonical unit (requests/second, users, "
        "ms, ratio, B; money keeps its currency). What the engines consume. Null without a constraint."
    )
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_requirement(cls, requirement: Requirement) -> RequirementResponse:
        content = requirement.content
        return cls(
            id=requirement.id,
            project_id=requirement.project_id,
            reference=requirement.reference,
            number=requirement.number,
            version=requirement.version,
            type=content.type,
            category=content.category,
            scope=content.scope,
            title=content.title,
            statement=content.statement,
            priority=content.priority,
            status=content.status,
            source=requirement.source,
            confidence=requirement.confidence,
            structured_data=content.structured_data,
            normalized_data=canonical_data(content.constraint),
            created_by_user_id=requirement.created_by_user_id,
            created_at=requirement.created_at,
            updated_at=requirement.updated_at,
        )


class RequirementVersionResponse(ApiModel):
    """One immutable state of a requirement. Versions are never modified or deleted."""

    requirement_id: uuid.UUID
    version: int
    type: RequirementType
    category: str
    scope: RequirementScope = Field(description="What it applies to; system also when unspecified.")
    title: str
    statement: str
    priority: RequirementPriority
    status: RequirementStatus
    source: RequirementSource
    confidence: Decimal | None
    structured_data: dict[str, Any]
    normalized_data: dict[str, Any] | None
    change_reason: str | None = Field(description="Why this version exists; null for version 1 and drafts.")
    created_by_user_id: uuid.UUID | None = Field(description="Who made this change.")
    created_at: datetime

    @classmethod
    def from_version(cls, version: RequirementVersion) -> RequirementVersionResponse:
        content = version.content
        return cls(
            requirement_id=version.requirement_id,
            version=version.version,
            type=content.type,
            category=content.category,
            scope=content.scope,
            title=content.title,
            statement=content.statement,
            priority=content.priority,
            status=content.status,
            source=version.source,
            confidence=version.confidence,
            structured_data=content.structured_data,
            normalized_data=canonical_data(content.constraint),
            change_reason=version.change_reason,
            created_by_user_id=version.created_by_user_id,
            created_at=version.created_at,
        )


class RequirementVersionPage(ApiModel):
    versions: list[RequirementVersionResponse]
    next_cursor: str | None = Field(description="Pass as ?cursor= for the next page; null at the end.")


class RequirementPage(ApiModel):
    requirements: list[RequirementResponse]
    next_cursor: str | None = Field(description="Pass as ?cursor= for the next page; null at the end.")


# Limits here only bound the request size; the domain enforces the exact rules.
class CreateRequirementRequest(RequestModel):
    type: RequirementType
    category: Annotated[str, Field(max_length=128, examples=["throughput"])]
    scope: RequirementScope = RequirementScope.SYSTEM
    title: Annotated[str, Field(max_length=400, examples=["API throughput"])]
    statement: Annotated[
        str, Field(max_length=10_000, examples=["The API must support 2,000 requests per second."])
    ]
    priority: RequirementPriority
    status: RequirementStatus = Field(
        default=RequirementStatus.DRAFT,
        description="draft or active; AI-sourced requirements start as draft.",
    )
    source: RequirementSource = RequirementSource.USER
    confidence: Decimal | str | None = Field(
        default=None, description="Required for source=ai, refused otherwise."
    )
    structured_data: dict[str, Any] = Field(default_factory=dict, description=STRUCTURED_DATA_DESCRIPTION)


class UpdateRequirementRequest(RequestModel):
    """Every change creates a new version. Type, source, confidence and project cannot change."""

    expected_version: Annotated[
        int, Field(ge=1, description="The version you edited (optimistic concurrency).")
    ]
    change_reason: Annotated[
        str | None,
        Field(max_length=1000, description="Required when the requirement is active or satisfied."),
    ] = None
    category: Annotated[str | None, Field(max_length=128)] = None
    scope: RequirementScope | None = None
    title: Annotated[str | None, Field(max_length=400)] = None
    statement: Annotated[str | None, Field(max_length=10_000)] = None
    priority: RequirementPriority | None = None
    status: RequirementStatus | None = None
    structured_data: dict[str, Any] | None = Field(
        default=None, description="Omit to keep; {} removes the constraint. " + STRUCTURED_DATA_DESCRIPTION
    )

    def to_changes(self) -> RequirementChanges:
        return RequirementChanges(
            category=self.category,
            scope=self.scope,
            title=self.title,
            statement=self.statement,
            priority=self.priority,
            status=self.status,
            structured_data=self.structured_data if self.structured_data is not None else KEEP,
        )


class RequirementRef(ApiModel):
    """Exactly which version a finding was computed from."""

    id: uuid.UUID
    reference: str
    version: int

    @classmethod
    def of(cls, requirement: Requirement) -> RequirementRef:
        return cls(id=requirement.id, reference=requirement.reference, version=requirement.version)


class IssueModel(ApiModel):
    severity: Severity
    field: str
    reason: str


class ValidationResponse(ApiModel):
    requirement: RequirementRef
    valid: bool = Field(description="No errors against today's rules in the current status.")
    ready_for_active: bool = Field(description="Would pass validation as an active requirement.")
    issues: list[IssueModel]

    @classmethod
    def from_report(cls, report: ValidationReport) -> ValidationResponse:
        return cls(
            requirement=RequirementRef.of(report.requirement),
            valid=report.valid,
            ready_for_active=report.ready_for_active,
            issues=[
                IssueModel(severity=i.severity, field=_camel_path(i.field), reason=i.reason)
                for i in report.issues
            ],
        )


class ConflictModel(ApiModel):
    reason: str = Field(description="disjoint_bounds or disjoint_sets")
    metric: str
    requirements: list[RequirementRef]
    message: str


class CoverageModel(ApiModel):
    concern: Concern
    requirements: list[RequirementRef]


class AmbiguityModel(ApiModel):
    requirement: RequirementRef
    reason: str = Field(description="missing_constraint, missing_percentile or low_confidence")


class UnboundedModel(ApiModel):
    metric: str
    reason: str
    requirements: list[RequirementRef]


class CompletenessModel(ApiModel):
    covered: list[CoverageModel]
    missing: list[Concern] = Field(
        description="Warnings, not errors: common concerns nobody has specified yet."
    )


class AnalysisResponse(ApiModel):
    requirements: list[RequirementRef] = Field(
        description="The draft, active and satisfied versions analyzed."
    )
    truncated: bool
    conflicts: list[ConflictModel]
    completeness: CompletenessModel
    ambiguous: list[AmbiguityModel]
    unbounded: list[UnboundedModel]

    @classmethod
    def from_analysis(cls, analysis: ProjectAnalysis) -> AnalysisResponse:
        refs = RequirementRef.of
        return cls(
            requirements=[refs(r) for r in analysis.requirements],
            truncated=analysis.truncated,
            conflicts=[
                ConflictModel(
                    reason=c.reason,
                    metric=c.metric,
                    requirements=[refs(r) for r in c.requirements],
                    message=c.message,
                )
                for c in analysis.conflicts
            ],
            completeness=CompletenessModel(
                covered=[
                    CoverageModel(concern=c.concern, requirements=[refs(r) for r in c.requirements])
                    for c in analysis.covered
                ],
                missing=list(analysis.missing),
            ),
            ambiguous=[
                AmbiguityModel(requirement=refs(a.requirement), reason=a.reason) for a in analysis.ambiguous
            ],
            unbounded=[
                UnboundedModel(
                    metric=u.metric, reason=u.reason, requirements=[refs(r) for r in u.requirements]
                )
                for u in analysis.unbounded
            ],
        )


def _camel_path(path: str) -> str:
    return ".".join(to_camel(part) for part in path.split("."))
