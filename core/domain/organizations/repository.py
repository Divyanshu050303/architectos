import uuid
from datetime import datetime
from typing import Protocol

from .entities import Membership, Organization, OrganizationWithRole
from .enums import Role


class OrganizationRepository(Protocol):
    async def add(self, *, name: str) -> Organization: ...

    async def rename(self, organization_id: uuid.UUID, name: str) -> Organization: ...

    async def soft_delete(self, organization_id: uuid.UUID, at: datetime) -> None: ...


class MembershipRepository(Protocol):
    async def add(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> Membership: ...

    async def get_in_active_organization(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationWithRole | None:
        """The caller's membership, found only if the organization exists, is not deleted and the
        user belongs to it: one query with all three conditions."""
        ...

    async def list_for_user(self, user_id: uuid.UUID) -> list[OrganizationWithRole]:
        """Active organizations the user belongs to, by name."""
        ...
