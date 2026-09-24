"""Organizations (tenants): create, list, read, rename, delete.

Every operation on an existing organization takes the caller's resolved ``Membership`` and
checks the required permission itself, in addition to the HTTP-layer check, so no future caller
can skip it.
"""

import uuid

from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.identity.entities import User
from core.domain.unit_of_work import UnitOfWork

from .entities import Membership, Organization, OrganizationWithRole
from .enums import Role
from .errors import EmailNotVerified, OrganizationNotFound
from .permissions import Permission
from .value_objects import normalize_organization_name


class OrganizationService:
    def __init__(self, uow: UnitOfWork, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._clock = clock

    async def create(self, *, user: User, name: str) -> OrganizationWithRole:
        """Creates the organization and the creator's OWNER membership in one transaction:
        if the membership cannot be written, the organization is rolled back with it."""
        if not user.is_email_verified:
            raise EmailNotVerified
        clean_name = normalize_organization_name(name)
        async with self._uow as uow:
            organization = await uow.organizations.add(name=clean_name)
            membership = await uow.memberships.add(
                organization_id=organization.id, user_id=user.id, role=Role.OWNER
            )
            await uow.audit.record(
                AuditEvent(
                    AuditAction.ORGANIZATION_CREATED,
                    actor_user_id=user.id,
                    organization_id=organization.id,
                    resource_type="organization",
                    resource_id=organization.id,
                    metadata={"name": organization.name},
                )
            )
        return OrganizationWithRole(organization=organization, membership=membership)

    async def list_for_user(self, *, user_id: uuid.UUID) -> list[OrganizationWithRole]:
        async with self._uow as uow:
            return await uow.memberships.list_for_user(user_id)

    async def resolve(self, *, organization_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationWithRole:
        """Tenant entry point: the authenticated user's membership in the organization, or 404."""
        async with self._uow as uow:
            found = await uow.memberships.get_in_active_organization(
                organization_id=organization_id, user_id=user_id
            )
        if found is None:
            raise OrganizationNotFound
        return found

    async def rename(self, *, membership: Membership, name: str) -> Organization:
        membership.require(Permission.ORGANIZATION_UPDATE)
        clean_name = normalize_organization_name(name)
        async with self._uow as uow:
            renamed = await uow.organizations.rename(membership.organization_id, clean_name)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.ORGANIZATION_UPDATED,
                    actor_user_id=membership.user_id,
                    organization_id=membership.organization_id,
                    resource_type="organization",
                    resource_id=membership.organization_id,
                    metadata={"name": renamed.name},
                )
            )
            return renamed

    async def delete(self, *, membership: Membership) -> None:
        """Soft delete: the tenant disappears for everyone (every lookup filters deleted
        organizations), while its rows and audit history are retained."""
        membership.require(Permission.ORGANIZATION_DELETE)
        async with self._uow as uow:
            await uow.organizations.soft_delete(membership.organization_id, self._clock())
            await uow.audit.record(
                AuditEvent(
                    AuditAction.ORGANIZATION_DELETED,
                    actor_user_id=membership.user_id,
                    organization_id=membership.organization_id,
                    resource_type="organization",
                    resource_id=membership.organization_id,
                )
            )
