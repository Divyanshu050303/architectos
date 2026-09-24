import uuid
from datetime import datetime

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.organizations.entities import (
    Membership,
    MemberView,
    Organization,
    OrganizationWithRole,
    OwnedOrganization,
)
from core.domain.organizations.enums import Role
from persistence.models import OrganizationMemberRecord, OrganizationRecord, UserRecord


def to_organization(record: OrganizationRecord) -> Organization:
    return Organization(
        id=record.id, name=record.name, created_at=record.created_at, updated_at=record.updated_at
    )


def to_membership(record: OrganizationMemberRecord) -> Membership:
    return Membership(
        id=record.id,
        organization_id=record.organization_id,
        user_id=record.user_id,
        role=Role(record.role),
        created_at=record.created_at,
    )


class SqlAlchemyOrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, *, name: str) -> Organization:
        record = OrganizationRecord(name=name)
        self._session.add(record)
        await self._session.flush()
        await self._session.refresh(record)
        return to_organization(record)

    async def rename(self, organization_id: uuid.UUID, name: str) -> Organization:
        record = await self._session.scalar(
            update(OrganizationRecord)
            .where(OrganizationRecord.id == organization_id, OrganizationRecord.deleted_at.is_(None))
            .values(name=name, updated_at=func.now())
            .returning(OrganizationRecord)
        )
        if record is None:
            msg = f"organization {organization_id} disappeared during the request"
            raise LookupError(msg)
        return to_organization(record)

    async def soft_delete(self, organization_id: uuid.UUID, at: datetime) -> None:
        await self._session.execute(
            update(OrganizationRecord)
            .where(OrganizationRecord.id == organization_id, OrganizationRecord.deleted_at.is_(None))
            .values(deleted_at=at, updated_at=func.now())
        )

    async def lock_active(self, organization_id: uuid.UUID) -> bool:
        locked = await self._session.scalar(
            select(OrganizationRecord.id)
            .where(OrganizationRecord.id == organization_id, OrganizationRecord.deleted_at.is_(None))
            .with_for_update()
        )
        return locked is not None


# Listing order: owners first, then by name.
_ROLE_ORDER = case(
    {"owner": 0, "admin": 1, "member": 2, "viewer": 3}, value=OrganizationMemberRecord.role, else_=4
)


class SqlAlchemyMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> Membership:
        record = OrganizationMemberRecord(organization_id=organization_id, user_id=user_id, role=role.value)
        self._session.add(record)
        await self._session.flush()
        await self._session.refresh(record)
        return to_membership(record)

    async def get_in_active_organization(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationWithRole | None:
        # Tenant scope, membership and liveness in one statement; served by the unique
        # (organization_id, user_id) index and the organizations primary key.
        row = (
            await self._session.execute(
                select(OrganizationRecord, OrganizationMemberRecord)
                .join(
                    OrganizationMemberRecord,
                    OrganizationMemberRecord.organization_id == OrganizationRecord.id,
                )
                .where(
                    OrganizationRecord.id == organization_id,
                    OrganizationRecord.deleted_at.is_(None),
                    OrganizationMemberRecord.user_id == user_id,
                )
            )
        ).first()
        if row is None:
            return None
        organization, member = row
        return OrganizationWithRole(
            organization=to_organization(organization), membership=to_membership(member)
        )

    async def list_for_user(self, user_id: uuid.UUID) -> list[OrganizationWithRole]:
        rows = await self._session.execute(
            select(OrganizationRecord, OrganizationMemberRecord)
            .join(OrganizationMemberRecord, OrganizationMemberRecord.organization_id == OrganizationRecord.id)
            .where(OrganizationMemberRecord.user_id == user_id, OrganizationRecord.deleted_at.is_(None))
            .order_by(func.lower(OrganizationRecord.name), OrganizationRecord.id)
        )
        return [
            OrganizationWithRole(organization=to_organization(org), membership=to_membership(member))
            for org, member in rows.all()
        ]

    async def get_for_user(self, *, organization_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None:
        record = await self._session.scalar(
            select(OrganizationMemberRecord)
            .where(
                OrganizationMemberRecord.organization_id == organization_id,
                OrganizationMemberRecord.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        )
        return to_membership(record) if record else None

    async def get(self, *, organization_id: uuid.UUID, membership_id: uuid.UUID) -> Membership | None:
        record = await self._session.scalar(
            select(OrganizationMemberRecord)
            .where(
                OrganizationMemberRecord.id == membership_id,
                OrganizationMemberRecord.organization_id == organization_id,
            )
            .execution_options(populate_existing=True)
        )
        return to_membership(record) if record else None

    async def count_owners(self, organization_id: uuid.UUID) -> int:
        count = await self._session.scalar(
            select(func.count()).where(
                OrganizationMemberRecord.organization_id == organization_id,
                OrganizationMemberRecord.role == Role.OWNER.value,
            )
        )
        return int(count or 0)

    async def list_members(self, organization_id: uuid.UUID) -> list[MemberView]:
        rows = await self._session.execute(
            select(OrganizationMemberRecord, UserRecord)
            .join(UserRecord, UserRecord.id == OrganizationMemberRecord.user_id)
            .where(OrganizationMemberRecord.organization_id == organization_id)
            .order_by(_ROLE_ORDER, func.lower(UserRecord.name), OrganizationMemberRecord.id)
        )
        return [
            MemberView(
                membership=to_membership(member), name=user.name, email=user.email, avatar_url=user.avatar_url
            )
            for member, user in rows.all()
        ]

    async def update_role(self, membership_id: uuid.UUID, role: Role) -> Membership:
        record = await self._session.scalar(
            update(OrganizationMemberRecord)
            .where(OrganizationMemberRecord.id == membership_id)
            .values(role=role.value, updated_at=func.now())
            .returning(OrganizationMemberRecord)
        )
        if record is None:
            msg = f"membership {membership_id} disappeared under the organization lock"
            raise LookupError(msg)
        return to_membership(record)

    async def delete(self, membership_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(OrganizationMemberRecord).where(OrganizationMemberRecord.id == membership_id)
        )

    async def owned_by(self, user_id: uuid.UUID) -> list[OwnedOrganization]:
        mine = (
            select(OrganizationMemberRecord.organization_id)
            .join(OrganizationRecord, OrganizationRecord.id == OrganizationMemberRecord.organization_id)
            .where(
                OrganizationMemberRecord.user_id == user_id,
                OrganizationMemberRecord.role == Role.OWNER.value,
                OrganizationRecord.deleted_at.is_(None),
            )
            .scalar_subquery()
        )
        rows = await self._session.execute(
            select(
                OrganizationMemberRecord.organization_id,
                func.count().filter(OrganizationMemberRecord.role == Role.OWNER.value),
                func.count(),
            )
            .where(OrganizationMemberRecord.organization_id.in_(mine))
            .group_by(OrganizationMemberRecord.organization_id)
            .order_by(OrganizationMemberRecord.organization_id)
        )
        return [
            OwnedOrganization(organization_id=org_id, owner_count=owners, member_count=members)
            for org_id, owners, members in rows.all()
        ]

    async def delete_all_for_user(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(OrganizationMemberRecord).where(OrganizationMemberRecord.user_id == user_id)
        )
