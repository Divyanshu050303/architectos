import uuid
from datetime import datetime
from typing import Protocol

from .entities import Membership, MemberView, Organization, OrganizationWithRole, OwnedOrganization
from .enums import Role


class OrganizationRepository(Protocol):
    async def add(self, *, name: str) -> Organization: ...

    async def rename(self, organization_id: uuid.UUID, name: str) -> Organization: ...

    async def soft_delete(self, organization_id: uuid.UUID, at: datetime) -> None: ...

    async def lock_active(self, organization_id: uuid.UUID) -> bool:
        """SELECT ... FOR UPDATE on the organization row. Every membership change takes this lock
        first, so changes to one organization's members are serialized. False if deleted/missing."""
        ...


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

    async def get_for_user(self, *, organization_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None: ...

    async def get(self, *, organization_id: uuid.UUID, membership_id: uuid.UUID) -> Membership | None:
        """Scoped by organization: another tenant's membership id is simply not found."""
        ...

    async def count_owners(self, organization_id: uuid.UUID) -> int: ...

    async def list_members(self, organization_id: uuid.UUID) -> list[MemberView]: ...

    async def update_role(self, membership_id: uuid.UUID, role: Role) -> Membership: ...

    async def delete(self, membership_id: uuid.UUID) -> None: ...

    async def owned_by(self, user_id: uuid.UUID) -> list[OwnedOrganization]:
        """Active organizations where the user is an owner, with owner and member counts."""
        ...

    async def delete_all_for_user(self, user_id: uuid.UUID) -> None: ...
