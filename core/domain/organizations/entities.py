import uuid
from dataclasses import dataclass
from datetime import datetime

from .enums import Role
from .errors import PermissionDenied
from .permissions import Permission, has_permission


@dataclass(frozen=True, slots=True)
class Organization:
    id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Membership:
    """A user's place in one organization. Every organization-scoped operation starts from one,
    resolved for the authenticated user (never from a client-supplied role or user id)."""

    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    role: Role
    created_at: datetime

    def can(self, permission: Permission) -> bool:
        return has_permission(self.role, permission)

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise PermissionDenied


@dataclass(frozen=True, slots=True)
class OrganizationWithRole:
    organization: Organization
    membership: Membership


@dataclass(frozen=True, slots=True)
class MemberView:
    """A membership with the member's public profile, as listed to the organization."""

    membership: Membership
    name: str
    email: str
    avatar_url: str | None


@dataclass(frozen=True, slots=True)
class OwnedOrganization:
    """For account deletion: an organization the user owns, and whether others depend on it."""

    organization_id: uuid.UUID
    owner_count: int
    member_count: int


@dataclass(frozen=True, slots=True)
class Invitation:
    """Stored invitation; the token itself exists only in the email."""

    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    role: Role
    invited_by_user_id: uuid.UUID | None
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime

    def is_pending(self) -> bool:
        return self.accepted_at is None and self.revoked_at is None
