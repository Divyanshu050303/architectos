import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from core.domain.audit.entities import AuditEntry
from core.domain.organizations.entities import Invitation, Membership, MemberView, OrganizationWithRole
from core.domain.organizations.enums import Role

from .common import ApiModel, RequestModel
from .fields import EmailInput


class OrganizationResponse(ApiModel):
    id: uuid.UUID
    name: str
    role: Role = Field(description="Your role in this organization.")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, scoped: OrganizationWithRole) -> OrganizationResponse:
        return cls(
            id=scoped.organization.id,
            name=scoped.organization.name,
            role=scoped.membership.role,
            created_at=scoped.organization.created_at,
            updated_at=scoped.organization.updated_at,
        )


class OrganizationList(ApiModel):
    organizations: list[OrganizationResponse]


class CreateOrganizationRequest(RequestModel):
    name: str = Field(max_length=200, examples=["Acme Engineering"])


class UpdateOrganizationRequest(RequestModel):
    name: str = Field(max_length=200)


class MemberResponse(ApiModel):
    id: uuid.UUID = Field(description="Membership id: use it to change the role or remove the member.")
    user_id: uuid.UUID
    name: str
    email: str
    avatar_url: str | None
    role: Role
    joined_at: datetime

    @classmethod
    def from_view(cls, view: MemberView) -> MemberResponse:
        return cls(
            id=view.membership.id,
            user_id=view.membership.user_id,
            name=view.name,
            email=view.email,
            avatar_url=view.avatar_url,
            role=view.membership.role,
            joined_at=view.membership.created_at,
        )


class MemberList(ApiModel):
    members: list[MemberResponse]


class ChangeRoleRequest(RequestModel):
    role: Role


class MembershipResponse(ApiModel):
    id: uuid.UUID
    user_id: uuid.UUID
    role: Role

    @classmethod
    def from_membership(cls, membership: Membership) -> MembershipResponse:
        return cls(id=membership.id, user_id=membership.user_id, role=membership.role)


class CreateInvitationRequest(RequestModel):
    email: EmailInput
    role: Role = Field(
        description="admin, member or viewer. Ownership is granted by changing a member's role."
    )


class InvitationResponse(ApiModel):
    """The token is never returned: it exists only in the invitee's email."""

    id: uuid.UUID
    email: str
    role: Role
    invited_by_user_id: uuid.UUID | None
    expires_at: datetime
    created_at: datetime

    @classmethod
    def from_invitation(cls, invitation: Invitation) -> InvitationResponse:
        return cls(
            id=invitation.id,
            email=invitation.email,
            role=invitation.role,
            invited_by_user_id=invitation.invited_by_user_id,
            expires_at=invitation.expires_at,
            created_at=invitation.created_at,
        )


class InvitationList(ApiModel):
    invitations: list[InvitationResponse]


class AuditEntryResponse(ApiModel):
    id: uuid.UUID
    action: str = Field(examples=["member.role_changed"])
    actor_user_id: uuid.UUID | None = Field(
        description="Null for system actions, e.g. token-reuse revocation."
    )
    resource_type: str | None
    resource_id: str | None
    metadata: dict[str, Any]
    ip_address: str | None
    user_agent: str | None
    created_at: datetime

    @classmethod
    def from_entry(cls, entry: AuditEntry) -> AuditEntryResponse:
        return cls(
            id=entry.id,
            action=entry.action,
            actor_user_id=entry.actor_user_id,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            metadata=entry.metadata,
            ip_address=entry.ip_address,
            user_agent=entry.user_agent,
            created_at=entry.created_at,
        )


class AuditLogPage(ApiModel):
    entries: list[AuditEntryResponse]
    next_cursor: str | None = Field(
        description="Pass as ?cursor= for the next (older) page; null at the end."
    )
