import uuid
from datetime import datetime

from pydantic import Field

from core.domain.organizations.entities import Membership, MemberView, OrganizationWithRole
from core.domain.organizations.enums import Role

from .common import ApiModel, RequestModel


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
