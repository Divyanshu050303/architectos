import uuid
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.permissions import CurrentMembership, require_permission
from apps.api.dependencies.services import (
    AuditServiceDep,
    InvitationServiceDep,
    MembershipServiceDep,
    OrganizationServiceDep,
    RateLimitsDep,
)
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.organization import (
    AuditEntryResponse,
    AuditLogPage,
    ChangeRoleRequest,
    CreateInvitationRequest,
    CreateOrganizationRequest,
    InvitationList,
    InvitationResponse,
    MemberList,
    MemberResponse,
    MembershipResponse,
    OrganizationList,
    OrganizationResponse,
    UpdateOrganizationRequest,
)
from core.domain.audit.audit_service import encode_cursor
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
    body: CreateOrganizationRequest,
    current: CurrentUser,
    organizations: OrganizationServiceDep,
    limits: RateLimitsDep,
) -> OrganizationResponse:
    await limits.enforce("create_organization", user_id=current.user.id)
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


# --- invitations --------------------------------------------------------------------------------


@router.get(
    "/{organization_id}/invitations",
    response_model=InvitationList,
    responses=SCOPED_ERRORS,
    dependencies=[require_permission(Permission.MEMBER_INVITE)],
    summary="Pending invitations",
    description="Unaccepted, unrevoked invitations, newest first (expired ones included; see expiresAt).",
)
async def list_invitations(scoped: CurrentMembership, invitations: InvitationServiceDep) -> InvitationList:
    found = await invitations.list_pending(membership=scoped.membership)
    return InvitationList(invitations=[InvitationResponse.from_invitation(i) for i in found])


@router.post(
    "/{organization_id}/invitations",
    status_code=status.HTTP_201_CREATED,
    response_model=InvitationResponse,
    responses=SCOPED_ERRORS
    | {
        403: {"model": ErrorResponse, "description": "permission_denied, role_not_manageable"},
        409: {"model": ErrorResponse, "description": "already_member"},
        422: {"model": ErrorResponse, "description": "invalid_email, owner_invitation_not_allowed"},
    },
    dependencies=[require_permission(Permission.MEMBER_INVITE)],
    summary="Invite someone by email",
    description=(
        "Emails a single-use link valid for INVITATION_TTL. Inviting an address with a pending "
        "invitation replaces it (the previous link stops working)."
    ),
)
async def create_invitation(
    body: CreateInvitationRequest,
    scoped: CurrentMembership,
    current: CurrentUser,
    invitations: InvitationServiceDep,
    limits: RateLimitsDep,
) -> InvitationResponse:
    await limits.enforce("create_invitation", user_id=current.user.id)
    invitation = await invitations.invite(
        inviter=current.user, organization_id=scoped.organization.id, email=body.email, role=body.role
    )
    return InvitationResponse.from_invitation(invitation)


@router.delete(
    "/{organization_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=SCOPED_ERRORS
    | {404: {"model": ErrorResponse, "description": "organization_not_found, invitation_not_found"}},
    dependencies=[require_permission(Permission.MEMBER_INVITE)],
    summary="Revoke a pending invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID, scoped: CurrentMembership, invitations: InvitationServiceDep
) -> None:
    await invitations.revoke(
        organization_id=scoped.organization.id,
        actor_user_id=scoped.membership.user_id,
        invitation_id=invitation_id,
    )


# --- audit log ----------------------------------------------------------------------------------


@router.get(
    "/{organization_id}/audit-log",
    response_model=AuditLogPage,
    responses=SCOPED_ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor"}},
    dependencies=[require_permission(Permission.AUDIT_READ)],
    summary="Security audit trail of an organization",
    description=(
        "Owners and admins. Newest first; follow nextCursor for older entries. Entries are append-only."
    ),
)
async def audit_log(
    scoped: CurrentMembership,
    audit: AuditServiceDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AuditLogPage:
    page = await audit.organization_log(membership=scoped.membership, cursor=cursor, limit=limit)
    return AuditLogPage(
        entries=[AuditEntryResponse.from_entry(e) for e in page.entries],
        next_cursor=encode_cursor(page.next_cursor) if page.next_cursor else None,
    )
