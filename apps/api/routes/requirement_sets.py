import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import RateLimitsDep, RequirementSetServiceDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.requirement_set import (
    CreateRequirementSetRequest,
    PlanningInputResponse,
    RequirementSetPage,
    RequirementSetResponse,
    RequirementSetSummary,
)
from core.domain.requirements.queries import decode_set_cursor

router = APIRouter(prefix="/projects/{project_id}/requirement-sets", tags=["requirement sets"])

SET_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, requirement_set_not_found"},
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=RequirementSetResponse,
    responses=SET_ERRORS
    | {
        409: {
            "model": ErrorResponse,
            "description": "project_archived, requirement_set_conflicts (details.conflicts)",
        },
        422: {
            "model": ErrorResponse,
            "description": "invalid_requirement_set (details: field or requirementId, and reason), "
            "validation_error",
        },
    },
    summary="Create a requirement set",
    description=(
        "An immutable, numbered snapshot (v1, v2, ...) pinning requirements at their current versions, "
        "with the Architecture Planning Input built from them. Only active or satisfied, valid, "
        "non-conflicting requirements can be pinned. Later requirement changes never alter a set."
    ),
)
async def create_requirement_set(
    project_id: uuid.UUID,
    body: CreateRequirementSetRequest,
    current: CurrentUser,
    sets: RequirementSetServiceDep,
    limits: RateLimitsDep,
) -> RequirementSetResponse:
    await limits.enforce("create_requirement_set", user_id=current.user.id)
    created = await sets.create(
        project_id=project_id,
        user_id=current.user.id,
        name=body.name,
        description=body.description,
        requirement_ids=body.requirement_ids,
    )
    return RequirementSetResponse.from_set(created)


@router.get(
    "",
    response_model=RequirementSetPage,
    responses=SET_ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor"}},
    summary="Requirement sets of a project",
    description="Newest first.",
)
async def list_requirement_sets(
    *,
    project_id: uuid.UUID,
    current: CurrentUser,
    sets: RequirementSetServiceDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RequirementSetPage:
    page = await sets.list(
        project_id=project_id,
        user_id=current.user.id,
        before=decode_set_cursor(cursor) if cursor else None,
        limit=limit,
    )
    return RequirementSetPage(
        requirement_sets=[RequirementSetSummary.from_set(s) for s in page.items], next_cursor=page.next_cursor
    )


@router.get(
    "/{set_id}",
    response_model=RequirementSetResponse,
    responses=SET_ERRORS,
    summary="A requirement set with its pinned versions",
)
async def get_requirement_set(
    project_id: uuid.UUID, set_id: uuid.UUID, current: CurrentUser, sets: RequirementSetServiceDep
) -> RequirementSetResponse:
    return RequirementSetResponse.from_set(
        await sets.get(project_id=project_id, set_id=set_id, user_id=current.user.id)
    )


@router.get(
    "/{set_id}/planning-input",
    response_model=PlanningInputResponse,
    responses=SET_ERRORS,
    summary="The Architecture Planning Input of a set",
    description="What the architecture engines consume, byte-for-byte as built when the set was created.",
)
async def get_planning_input(
    project_id: uuid.UUID, set_id: uuid.UUID, current: CurrentUser, sets: RequirementSetServiceDep
) -> PlanningInputResponse:
    requirement_set, document = await sets.planning_input(
        project_id=project_id, set_id=set_id, user_id=current.user.id
    )
    return PlanningInputResponse(
        requirement_set_id=requirement_set.id,
        schema_version=requirement_set.schema_version,
        content_hash=requirement_set.content_hash,
        planning_input=document,
    )
