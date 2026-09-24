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
