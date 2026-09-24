import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.organizations.entities import Membership, Organization, OrganizationWithRole
from core.domain.organizations.enums import Role
from persistence.models import OrganizationMemberRecord, OrganizationRecord


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
