import uuid
from dataclasses import replace

from fastapi import APIRouter, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.permissions import CurrentMembership, require_permission
from apps.api.dependencies.services import MembershipServiceDep, OrganizationServiceDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.organization import (
    ChangeRoleRequest,
    CreateOrganizationRequest,
    MemberList,
    MemberResponse,
    MembershipResponse,
    OrganizationList,
    OrganizationResponse,
    UpdateOrganizationRequest,
)
from core.domain.organizations.permissions import Permission

router = APIRouter(prefix="/organizations", tags=["organizations"])

SCOPED_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "organization_not_found (also when you are not a member)"},
}


@router.get("", response_model=OrganizationList, summary="Organizations you belong to")
async def list_organizations(current: CurrentUser, organizations: OrganizationServiceDep) -> OrganizationList:
    found = await organizations.list_for_user(user_id=current.user.id)
    return OrganizationList(organizations=[OrganizationResponse.from_domain(o) for o in found])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=OrganizationResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse, "description": "email_not_verified"},
        422: {"model": ErrorResponse, "description": "invalid_organization_name"},
    },
    summary="Create an organization",
    description="You become its owner. Requires a verified email address.",
)
async def create_organization(
    body: CreateOrganizationRequest, current: CurrentUser, organizations: OrganizationServiceDep
) -> OrganizationResponse:
    created = await organizations.create(user=current.user, name=body.name)
    return OrganizationResponse.from_domain(created)


@router.get(
    "/{organization_id}",
    response_model=OrganizationResponse,
    responses=SCOPED_ERRORS,
    dependencies=[require_permission(Permission.ORGANIZATION_READ)],
    summary="An organization you belong to",
)
async def get_organization(scoped: CurrentMembership) -> OrganizationResponse:
    return OrganizationResponse.from_domain(scoped)


@router.patch(
    "/{organization_id}",
    response_model=OrganizationResponse,
    responses=SCOPED_ERRORS | {422: {"model": ErrorResponse, "description": "invalid_organization_name"}},
    dependencies=[require_permission(Permission.ORGANIZATION_UPDATE)],
    summary="Rename an organization",
    description="Owners and admins.",
)
async def update_organization(
    body: UpdateOrganizationRequest, scoped: CurrentMembership, organizations: OrganizationServiceDep
) -> OrganizationResponse:
    renamed = await organizations.rename(membership=scoped.membership, name=body.name)
    return OrganizationResponse.from_domain(replace(scoped, organization=renamed))


@router.delete(
    "/{organization_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=SCOPED_ERRORS,
    dependencies=[require_permission(Permission.ORGANIZATION_DELETE)],
    summary="Delete an organization",
    description="Owners only. The organization disappears for every member; its history is retained.",
)
async def delete_organization(scoped: CurrentMembership, organizations: OrganizationServiceDep) -> None:
    await organizations.delete(membership=scoped.membership)


# --- members ------------------------------------------------------------------------------------


@router.get(
    "/{organization_id}/members",
    response_model=MemberList,
    responses=SCOPED_ERRORS,
    dependencies=[require_permission(Permission.MEMBER_READ)],
    summary="Members of an organization",
    description="Owners first, then by name.",
)
async def list_members(scoped: CurrentMembership, members: MembershipServiceDep) -> MemberList:
    found = await members.list_members(membership=scoped.membership)
    return MemberList(members=[MemberResponse.from_view(view) for view in found])


@router.patch(
    "/{organization_id}/members/{member_id}",
    response_model=MembershipResponse,
    responses=SCOPED_ERRORS
    | {
        403: {
            "model": ErrorResponse,
            "description": "permission_denied, cannot_change_own_role, role_not_manageable",
        },
        404: {"model": ErrorResponse, "description": "organization_not_found, member_not_found"},
        409: {"model": ErrorResponse, "description": "last_owner"},
    },
    dependencies=[require_permission(Permission.MEMBER_UPDATE_ROLE)],
    summary="Change a member's role",
    description=(
        "Owners may change anyone's role, including granting ownership. Admins may change members and "
        "viewers to member or viewer. Nobody changes their own role; the last owner cannot be demoted."
    ),
)
async def change_role(
    member_id: uuid.UUID,
    body: ChangeRoleRequest,
    scoped: CurrentMembership,
    members: MembershipServiceDep,
) -> MembershipResponse:
    updated = await members.change_role(
        organization_id=scoped.organization.id,
        actor_user_id=scoped.membership.user_id,
        member_id=member_id,
        role=body.role,
    )
    return MembershipResponse.from_membership(updated)


@router.delete(
    "/{organization_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=SCOPED_ERRORS
    | {
        403: {"model": ErrorResponse, "description": "permission_denied, role_not_manageable"},
        404: {"model": ErrorResponse, "description": "organization_not_found, member_not_found"},
        409: {"model": ErrorResponse, "description": "last_owner"},
    },
    summary="Remove a member, or leave",
    description=(
        "Removing your own membership is leaving and needs no permission. Removing someone else needs "
        "member.remove and a role above theirs (owners may remove owners). The last owner cannot leave."
    ),
)
async def remove_member(
    member_id: uuid.UUID, scoped: CurrentMembership, members: MembershipServiceDep
) -> None:
    await members.remove(
        organization_id=scoped.organization.id, actor_user_id=scoped.membership.user_id, member_id=member_id
    )
