import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import RateLimitsDep, RequirementServiceDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.requirement import (
    CreateRequirementRequest,
    RequirementPage,
    RequirementResponse,
    UpdateRequirementRequest,
)
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.queries import MAX_SEARCH_LENGTH, RequirementCursor, RequirementQuery

router = APIRouter(prefix="/projects/{project_id}/requirements", tags=["requirements"])

# Authorization happens inside each service call, in the same transaction as the work: the project
# is resolved through the caller's membership (404 for other tenants), then the permission checked.
REQUIREMENT_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, requirement_not_found (also for other tenants and projects)",
    },
}
WRITE_ERRORS = REQUIREMENT_ERRORS | {
    409: {"model": ErrorResponse, "description": "project_archived"},
    422: {
        "model": ErrorResponse,
        "description": "invalid_requirement (details: field, reason), validation_error",
    },
}


@router.get(
    "",
    response_model=RequirementPage,
    responses=REQUIREMENT_ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor"}},
    summary="Requirements of a project",
    description=(
        "Live requirements, newest first, paginated. Filters combine; search matches title or statement."
    ),
)
async def list_requirements(  # noqa: PLR0913 - one parameter per query filter
    *,
    project_id: uuid.UUID,
    current: CurrentUser,
    requirements: RequirementServiceDep,
    type_filter: Annotated[RequirementType | None, Query(alias="type")] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    status_filter: Annotated[RequirementStatus | None, Query(alias="status")] = None,
    priority: Annotated[RequirementPriority | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=MAX_SEARCH_LENGTH)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RequirementPage:
    query = RequirementQuery(
        type=type_filter,
        category=category.strip().lower() if category else None,
        status=status_filter,
        priority=priority,
        search=search,
        after=RequirementCursor.decode(cursor) if cursor else None,
        limit=limit,
    )
    page = await requirements.list(project_id=project_id, user_id=current.user.id, query=query)
    return RequirementPage(
        requirements=[RequirementResponse.from_requirement(r) for r in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=RequirementResponse,
    responses=WRITE_ERRORS,
    summary="Create a requirement",
    description="Validated deterministically; stored as version 1. Not allowed in archived projects.",
)
async def create_requirement(
    project_id: uuid.UUID,
    body: CreateRequirementRequest,
    current: CurrentUser,
    requirements: RequirementServiceDep,
    limits: RateLimitsDep,
) -> RequirementResponse:
    await limits.enforce("create_requirement", user_id=current.user.id)
    requirement = await requirements.create(
        project_id=project_id,
        user_id=current.user.id,
        type=body.type,
        category=body.category,
        title=body.title,
        statement=body.statement,
        priority=body.priority,
        status=body.status,
        source=body.source,
        confidence=body.confidence,
        structured_data=body.structured_data,
    )
    return RequirementResponse.from_requirement(requirement)


@router.get(
    "/{requirement_id}",
    response_model=RequirementResponse,
    responses=REQUIREMENT_ERRORS,
    summary="A requirement",
)
async def get_requirement(
    project_id: uuid.UUID,
    requirement_id: uuid.UUID,
    current: CurrentUser,
    requirements: RequirementServiceDep,
) -> RequirementResponse:
    requirement = await requirements.get(
        project_id=project_id, requirement_id=requirement_id, user_id=current.user.id
    )
    return RequirementResponse.from_requirement(requirement)


@router.patch(
    "/{requirement_id}",
    response_model=RequirementResponse,
    responses=WRITE_ERRORS
    | {
        409: {
            "model": ErrorResponse,
            "description": "project_archived, requirement_version_conflict (details.currentVersion), "
            "invalid_status_transition, requirement_locked",
        },
        422: {
            "model": ErrorResponse,
            "description": "invalid_requirement, change_reason_required, nothing_to_update, validation_error",
        },
    },
    summary="Change a requirement",
    description=(
        "Creates a new immutable version (the previous state is kept in the history). Send the "
        "version you edited as expectedVersion. Active and satisfied requirements need a "
        "changeReason. Status: draft -> active -> satisfied; active -> invalid; invalid -> draft; "
        "satisfied -> active (reopen); any but deprecated -> deprecated (final)."
    ),
)
async def update_requirement(
    project_id: uuid.UUID,
    requirement_id: uuid.UUID,
    body: UpdateRequirementRequest,
    current: CurrentUser,
    requirements: RequirementServiceDep,
) -> RequirementResponse:
    requirement = await requirements.update(
        project_id=project_id,
        requirement_id=requirement_id,
        user_id=current.user.id,
        expected_version=body.expected_version,
        changes=body.to_changes(),
        change_reason=body.change_reason,
    )
    return RequirementResponse.from_requirement(requirement)


@router.delete(
    "/{requirement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=REQUIREMENT_ERRORS | {409: {"model": ErrorResponse, "description": "project_archived"}},
    summary="Delete a requirement",
    description="A soft delete: the requirement leaves the project; its version history is retained.",
)
async def delete_requirement(
    project_id: uuid.UUID,
    requirement_id: uuid.UUID,
    current: CurrentUser,
    requirements: RequirementServiceDep,
) -> None:
    await requirements.delete(project_id=project_id, requirement_id=requirement_id, user_id=current.user.id)
