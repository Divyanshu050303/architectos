"""Membership changes: list, change role, remove (including leaving).

Every change runs in one transaction that first locks the organization row, then re-reads the
actor's and the target's memberships under that lock. Decisions are therefore made on current
data: two owners demoting each other at the same moment cannot leave the organization ownerless.
"""

import uuid
from datetime import datetime

from core.domain.unit_of_work import UnitOfWork

from .entities import Membership, MemberView
from .enums import Role
from .errors import MemberNotFound, OrganizationNotFound, SoleOwnerOfOrganization
from .membership_policy import check_removal, check_role_change
from .permissions import Permission


class MembershipService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list_members(self, *, membership: Membership) -> list[MemberView]:
        membership.require(Permission.MEMBER_READ)
        async with self._uow as uow:
            return await uow.memberships.list_members(membership.organization_id)

    async def change_role(
        self, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, member_id: uuid.UUID, role: Role
    ) -> Membership:
        async with self._uow as uow:
            actor, target = await self._lock_and_load(uow, organization_id, actor_user_id, member_id)
            check_role_change(
                actor=actor.role,
                target=target.role,
                new_role=role,
                is_self=actor.id == target.id,
                owner_count=await uow.memberships.count_owners(organization_id),
            )
            if target.role is role:
                return target
            return await uow.memberships.update_role(target.id, role)

    async def remove(
        self, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, member_id: uuid.UUID
    ) -> None:
        async with self._uow as uow:
            actor, target = await self._lock_and_load(uow, organization_id, actor_user_id, member_id)
            check_removal(
                actor=actor.role,
                target=target.role,
                is_self=actor.id == target.id,
                owner_count=await uow.memberships.count_owners(organization_id),
            )
            await uow.memberships.delete(target.id)

    @staticmethod
    async def _lock_and_load(
        uow: UnitOfWork, organization_id: uuid.UUID, actor_user_id: uuid.UUID, member_id: uuid.UUID
    ) -> tuple[Membership, Membership]:
        if not await uow.organizations.lock_active(organization_id):
            raise OrganizationNotFound
        actor = await uow.memberships.get_for_user(organization_id=organization_id, user_id=actor_user_id)
        if actor is None:
            raise OrganizationNotFound
        target = await uow.memberships.get(organization_id=organization_id, membership_id=member_id)
        if target is None:
            raise MemberNotFound
        return actor, target


async def release_memberships(uow: UnitOfWork, *, user_id: uuid.UUID, at: datetime) -> None:
    """Called inside account deletion's transaction. Refuses if the user is the only owner of an
    organization other people belong to; soft-deletes organizations the user is alone in; removes
    the user's remaining memberships."""
    owned = await uow.memberships.owned_by(user_id)
    for organization in owned:
        await uow.organizations.lock_active(organization.organization_id)
    blocking = [o for o in owned if o.owner_count == 1 and o.member_count > 1]
    if blocking:
        raise SoleOwnerOfOrganization(details={"organizationIds": [str(o.organization_id) for o in blocking]})
    for organization in owned:
        if organization.member_count == 1:
            await uow.organizations.soft_delete(organization.organization_id, at)
    await uow.memberships.delete_all_for_user(user_id)
