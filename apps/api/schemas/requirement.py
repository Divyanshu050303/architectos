import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import Field

from core.domain.requirements.entities import Requirement, RequirementChanges
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementSource,
    RequirementStatus,
    RequirementType,
)
from core.domain.requirements.value_objects import KEEP

from .common import ApiModel, RequestModel

STRUCTURED_DATA_DESCRIPTION = (
    "One structured constraint, or {} for none. Quantity: {metric, operator (>=, >, <=, <), value, "
    'unit, percentile?}, e.g. {"metric": "latency", "operator": "<=", "value": "300", '
    '"unit": "ms", "percentile": "95"}. Set: {metric, operator: "in", values: [...]}. '
    "Numbers are exact decimals: send them as strings to avoid binary floating point; they are "
    "always returned as strings."
)


class RequirementResponse(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    reference: str = Field(examples=["REQ-12"], description="Stable, human-readable, unique in the project.")
    number: int
    version: int = Field(description="Current version; send it back as expectedVersion when updating.")
    type: RequirementType
    category: str
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
            title=content.title,
            statement=content.statement,
            priority=content.priority,
            status=content.status,
            source=requirement.source,
            confidence=requirement.confidence,
            structured_data=content.structured_data,
            created_by_user_id=requirement.created_by_user_id,
            created_at=requirement.created_at,
            updated_at=requirement.updated_at,
        )


class RequirementPage(ApiModel):
    requirements: list[RequirementResponse]
    next_cursor: str | None = Field(description="Pass as ?cursor= for the next page; null at the end.")


# Limits here only bound the request size; the domain enforces the exact rules.
class CreateRequirementRequest(RequestModel):
    type: RequirementType
    category: Annotated[str, Field(max_length=128, examples=["throughput"])]
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
            title=self.title,
            statement=self.statement,
            priority=self.priority,
            status=self.status,
            structured_data=self.structured_data if self.structured_data is not None else KEEP,
        )
