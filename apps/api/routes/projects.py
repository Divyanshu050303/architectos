from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.permissions import (
    CurrentMembership,
    CurrentProject,
    require_permission,
    require_project_permission,
)
from apps.api.dependencies.services import ProjectServiceDep, RateLimitsDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.project import (
    ArchitecturePolicyRequest,
    ArchitecturePolicyResponse,
    CreateProjectRequest,
    ProjectPage,
    ProjectResponse,
    UpdateProjectRequest,
    settings_or_none,
)
from core.domain.organizations.permissions import Permission
from core.domain.projects.enums import ProjectStatus
from core.domain.projects.queries import MAX_SEARCH_LENGTH, ProjectCursor, ProjectQuery, ProjectSort

router = APIRouter(tags=["projects"])

PROJECT_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found (also for other tenants' projects)"},
}


@router.get(
    "/organizations/{organization_id}/projects",
    response_model=ProjectPage,
    responses=PROJECT_ERRORS
    | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    dependencies=[require_permission(Permission.PROJECT_READ)],
    summary="Projects of an organization",
    description=(
        "Live (not deleted) projects, paginated. Sort by createdAt (newest first, default), updatedAt "
        "(most recent first) or name (A-Z). search matches name or slug, case-insensitively."
    ),
)
async def list_projects(
    scoped: CurrentMembership,
    projects: ProjectServiceDep,
    status_filter: Annotated[ProjectStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=MAX_SEARCH_LENGTH)] = None,
    sort: Annotated[ProjectSort, Query()] = ProjectSort.CREATED_AT,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ProjectPage:
    query = ProjectQuery(
        status=status_filter,
        search=search,
        sort=sort,
        after=ProjectCursor.decode(cursor, sort=sort) if cursor else None,
        limit=limit,
    )
    page = await projects.list(membership=scoped.membership, query=query)
    role = scoped.membership.role
    return ProjectPage(
        projects=[ProjectResponse.from_project(p, role=role) for p in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/organizations/{organization_id}/projects",
    status_code=status.HTTP_201_CREATED,
    response_model=ProjectResponse,
    responses=PROJECT_ERRORS
    | {
        409: {"model": ErrorResponse, "description": "project_slug_taken"},
        422: {"model": ErrorResponse, "description": "invalid_project_name/slug/description/settings"},
    },
    dependencies=[require_permission(Permission.PROJECT_CREATE)],
    summary="Create a project",
    description=(
        "The slug is derived from the name unless given; it is unique in the organization and immutable."
    ),
)
async def create_project(
    body: CreateProjectRequest, scoped: CurrentMembership, projects: ProjectServiceDep, limits: RateLimitsDep
) -> ProjectResponse:
    await limits.enforce("create_project", user_id=scoped.membership.user_id)
    project = await projects.create(
        membership=scoped.membership,
        name=body.name,
        slug=body.slug,
        description=body.description,
        settings=settings_or_none(body.settings),
    )
    return ProjectResponse.from_project(project, role=scoped.membership.role)


@router.get(
    "/projects/{project_id}",
    response_model=ProjectResponse,
    responses=PROJECT_ERRORS,
    dependencies=[require_project_permission(Permission.PROJECT_READ)],
    summary="A project",
)
async def get_project(scoped: CurrentProject) -> ProjectResponse:
    return ProjectResponse.from_access(scoped)


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectResponse,
    responses=PROJECT_ERRORS
    | {
        409: {"model": ErrorResponse, "description": "project_archived"},
        422: {"model": ErrorResponse, "description": "nothing_to_update, invalid_project_*"},
    },
    dependencies=[require_project_permission(Permission.PROJECT_UPDATE)],
    summary="Update a project",
    description="Name, description and settings. Archived projects are read-only (restore first).",
)
async def update_project(
    body: UpdateProjectRequest, scoped: CurrentProject, projects: ProjectServiceDep
) -> ProjectResponse:
    access = await projects.update(
        project_id=scoped.project.id,
        user_id=scoped.membership.user_id,
        name=body.name,
        description=body.description,
        settings=settings_or_none(body.settings),
    )
    return ProjectResponse.from_access(access)


# --- architecture policy ------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/architecture-policy",
    response_model=ArchitecturePolicyResponse,
    responses=PROJECT_ERRORS,
    dependencies=[require_project_permission(Permission.PROJECT_READ)],
    summary="The project's architecture policy",
    description="What validation enforces for every architecture of the project.",
)
async def get_architecture_policy(scoped: CurrentProject) -> ArchitecturePolicyResponse:
    return ArchitecturePolicyResponse.from_access(scoped)


@router.put(
    "/projects/{project_id}/architecture-policy",
    response_model=ArchitecturePolicyResponse,
    responses=PROJECT_ERRORS
    | {
        409: {"model": ErrorResponse, "description": "project_archived"},
        422: {"model": ErrorResponse, "description": "invalid_architecture_policy, validation_error"},
    },
    dependencies=[require_project_permission(Permission.PROJECT_POLICY_UPDATE)],
    summary="Replace the project's architecture policy",
    description=(
        "Owners and admins. The whole policy is replaced; fields left out constrain nothing. "
        "Existing validation runs keep the policy they were run with."
    ),
)
async def put_architecture_policy(
    body: ArchitecturePolicyRequest, scoped: CurrentProject, projects: ProjectServiceDep
) -> ArchitecturePolicyResponse:
    access = await projects.update_policy(
        project_id=scoped.project.id, user_id=scoped.membership.user_id, policy=body.to_domain()
    )
    return ArchitecturePolicyResponse.from_access(access)


# --- lifecycle ----------------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/archive",
    response_model=ProjectResponse,
    responses=PROJECT_ERRORS,
    dependencies=[require_project_permission(Permission.PROJECT_ARCHIVE)],
    summary="Archive a project",
    description="Owners and admins. The project becomes read-only. Idempotent.",
)
async def archive_project(scoped: CurrentProject, projects: ProjectServiceDep) -> ProjectResponse:
    access = await projects.archive(project_id=scoped.project.id, user_id=scoped.membership.user_id)
    return ProjectResponse.from_access(access)


@router.post(
    "/projects/{project_id}/restore",
    response_model=ProjectResponse,
    responses=PROJECT_ERRORS,
    dependencies=[require_project_permission(Permission.PROJECT_ARCHIVE)],
    summary="Restore an archived project",
    description="Owners and admins. Makes the project editable again. Idempotent.",
)
async def restore_project(scoped: CurrentProject, projects: ProjectServiceDep) -> ProjectResponse:
    access = await projects.restore(project_id=scoped.project.id, user_id=scoped.membership.user_id)
    return ProjectResponse.from_access(access)


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=PROJECT_ERRORS | {409: {"model": ErrorResponse, "description": "project_not_archived"}},
    dependencies=[require_project_permission(Permission.PROJECT_DELETE)],
    summary="Delete an archived project",
    description=(
        "Owners and admins, archived projects only (archive first). A soft delete: the project "
        "disappears everywhere and its slug can be reused; its history is retained."
    ),
)
async def delete_project(scoped: CurrentProject, projects: ProjectServiceDep) -> None:
    await projects.delete(project_id=scoped.project.id, user_id=scoped.membership.user_id)
