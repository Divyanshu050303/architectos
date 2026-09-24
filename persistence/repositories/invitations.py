import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.organizations.entities import Invitation
from core.domain.organizations.enums import Role
from persistence.models import InvitationRecord


def to_invitation(record: InvitationRecord) -> Invitation:
    return Invitation(
        id=record.id,
        organization_id=record.organization_id,
        email=record.email,
        role=Role(record.role),
        invited_by_user_id=record.invited_by_user_id,
        expires_at=record.expires_at,
        accepted_at=record.accepted_at,
        revoked_at=record.revoked_at,
        created_at=record.created_at,
    )


_PENDING = (InvitationRecord.accepted_at.is_(None), InvitationRecord.revoked_at.is_(None))


class SqlAlchemyInvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        organization_id: uuid.UUID,
        email: str,
        role: Role,
        token_hash: bytes,
        invited_by_user_id: uuid.UUID,
        expires_at: datetime,
        created_at: datetime,
    ) -> Invitation:
        record = InvitationRecord(
            organization_id=organization_id,
            email=email,
            role=role.value,
            token_hash=token_hash,
            invited_by_user_id=invited_by_user_id,
            expires_at=expires_at,
            created_at=created_at,
        )
        self._session.add(record)
        await self._session.flush()
        await self._session.refresh(record)
        return to_invitation(record)

    async def list_pending(self, organization_id: uuid.UUID) -> list[Invitation]:
        # Served by ix_invitations_organization_id_created_at.
        records = await self._session.scalars(
            select(InvitationRecord)
            .where(InvitationRecord.organization_id == organization_id, *_PENDING)
            .order_by(InvitationRecord.created_at.desc(), InvitationRecord.id.desc())
        )
        return [to_invitation(r) for r in records]

    async def get_pending(self, *, organization_id: uuid.UUID, invitation_id: uuid.UUID) -> Invitation | None:
        record = await self._session.scalar(
            select(InvitationRecord).where(
                InvitationRecord.id == invitation_id,
                InvitationRecord.organization_id == organization_id,
                *_PENDING,
            )
        )
        return to_invitation(record) if record else None

    async def get_by_hash_for_update(self, token_hash: bytes) -> Invitation | None:
        record = await self._session.scalar(
            select(InvitationRecord)
            .where(InvitationRecord.token_hash == token_hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return to_invitation(record) if record else None

    async def revoke_pending_for_email(self, *, organization_id: uuid.UUID, email: str, at: datetime) -> None:
        await self._session.execute(
            update(InvitationRecord)
            .where(
                InvitationRecord.organization_id == organization_id,
                func.lower(InvitationRecord.email) == email,
                *_PENDING,
            )
            .values(revoked_at=at, updated_at=func.now())
        )

    async def revoke(self, invitation_id: uuid.UUID, at: datetime) -> None:
        await self._session.execute(
            update(InvitationRecord)
            .where(InvitationRecord.id == invitation_id, *_PENDING)
            .values(revoked_at=at, updated_at=func.now())
        )

    async def mark_accepted(self, invitation_id: uuid.UUID, *, user_id: uuid.UUID, at: datetime) -> None:
        await self._session.execute(
            update(InvitationRecord)
            .where(InvitationRecord.id == invitation_id)
            .values(accepted_at=at, accepted_by_user_id=user_id, updated_at=func.now())
        )
