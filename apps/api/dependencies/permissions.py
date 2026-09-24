"""Organization scoping and authorization for routes.

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

from .auth import CurrentUser
from .services import OrganizationServiceDep


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
