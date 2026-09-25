"""Organization and project scoping and authorization for routes.

    @router.patch("/{organization_id}", dependencies=[require_permission(Permission.ORGANIZATION_UPDATE)])

``get_current_membership`` resolves the authenticated user's membership in the organization named
by the path (never a client-supplied role or user id). A non-member gets 404, exactly like a
missing organization. ``require_permission`` then checks the central role matrix: 403 on failure.
"""

import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends

from core.domain.organizations.entities import Membership, OrganizationWithRole
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess

from .auth import CurrentUser
from .services import OrganizationServiceDep, ProjectServiceDep


async def get_current_membership(
    organization_id: uuid.UUID, current: CurrentUser, organizations: OrganizationServiceDep
) -> OrganizationWithRole:
    return await organizations.resolve(organization_id=organization_id, user_id=current.user.id)


CurrentMembership = Annotated[OrganizationWithRole, Depends(get_current_membership)]


def require_permission(permission: Permission) -> Any:
    async def check(scoped: CurrentMembership) -> Membership:
        scoped.membership.require(permission)
        return scoped.membership

    check.__name__ = f"require_{permission.value.replace('.', '_')}"
    dependency: Callable[..., Coroutine[Any, Any, Membership]] = check
    return Depends(dependency)


async def get_current_project(
    project_id: uuid.UUID, current: CurrentUser, projects: ProjectServiceDep
) -> ProjectAccess:
    """The project named by the path, resolved together with the caller's membership in its
    organization. Another tenant's project, a deleted one, or a missing one: 404 alike."""
    return await projects.resolve(project_id=project_id, user_id=current.user.id)


CurrentProject = Annotated[ProjectAccess, Depends(get_current_project)]


def require_project_permission(permission: Permission) -> Any:  # returns a FastAPI Depends(...) marker
    async def check(scoped: CurrentProject) -> ProjectAccess:
        scoped.membership.require(permission)
        return scoped

    check.__name__ = f"require_project_{permission.value.replace('.', '_')}"
    dependency: Callable[..., Coroutine[Any, Any, ProjectAccess]] = check
    return Depends(dependency)
