import uuid
from datetime import datetime

from pydantic import Field

from core.domain.organizations.entities import OrganizationWithRole
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
