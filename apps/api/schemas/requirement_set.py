import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from core.domain.requirements.requirement_sets import MAX_SET_REQUIREMENTS, PinnedVersion, RequirementSet

from .common import ApiModel, RequestModel


class PinnedVersionModel(ApiModel):
    requirement_id: uuid.UUID
    reference: str
    version: int

    @classmethod
    def of(cls, item: PinnedVersion) -> PinnedVersionModel:
        return cls(requirement_id=item.requirement_id, reference=item.reference, version=item.version)


class RequirementSetSummary(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    number: int
    label: str = Field(examples=["v3"])
    name: str
    description: str
    schema_version: int = Field(description="Version of the Architecture Planning Input contract.")
    content_hash: str = Field(
        description="SHA-256 of the planning input's canonical JSON: equal requirements, equal hash."
    )
    requirement_count: int
    created_by_user_id: uuid.UUID | None
    created_at: datetime

    @classmethod
    def fields_of(cls, requirement_set: RequirementSet) -> dict[str, Any]:
        return {
            "id": requirement_set.id,
            "project_id": requirement_set.project_id,
            "number": requirement_set.number,
            "label": requirement_set.label,
            "name": requirement_set.name,
            "description": requirement_set.description,
            "schema_version": requirement_set.schema_version,
            "content_hash": requirement_set.content_hash,
            "requirement_count": requirement_set.requirement_count,
            "created_by_user_id": requirement_set.created_by_user_id,
            "created_at": requirement_set.created_at,
        }

    @classmethod
    def from_set(cls, requirement_set: RequirementSet) -> RequirementSetSummary:
        return cls(**cls.fields_of(requirement_set))


class RequirementSetResponse(RequirementSetSummary):
    requirements: list[PinnedVersionModel] = Field(description="The pinned versions, by requirement number.")

    @classmethod
    def from_set(cls, requirement_set: RequirementSet) -> RequirementSetResponse:
        return cls(
            **cls.fields_of(requirement_set),
            requirements=[PinnedVersionModel.of(item) for item in requirement_set.items],
        )


class RequirementSetPage(ApiModel):
    requirement_sets: list[RequirementSetSummary]
    next_cursor: str | None = Field(description="Pass as ?cursor= for the next page; null at the end.")


class PlanningInputResponse(ApiModel):
    requirement_set_id: uuid.UUID
    schema_version: int
    content_hash: str
    planning_input: dict[str, Any] = Field(
        description="The Architecture Planning Input exactly as stored at creation: a versioned engine "
        "contract with snake_case keys (see core/domain/requirements/planning.py)."
    )


class CreateRequirementSetRequest(RequestModel):
    name: Annotated[str, Field(max_length=200, examples=["Launch baseline"])] = ""
    description: Annotated[str, Field(max_length=4000)] = ""
    requirement_ids: Annotated[
        list[uuid.UUID] | None,
        Field(
            max_length=MAX_SET_REQUIREMENTS,
            description="Requirements to pin at their current versions (active or satisfied). "
            "Omit to pin every requirement in force.",
        ),
    ] = None
