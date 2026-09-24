import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.identity.entities import NewSession, Session
from core.domain.identity.enums import SessionRevocationReason
from persistence.models import SessionRecord


def to_session(record: SessionRecord) -> Session:
    return Session(
        id=record.id,
        user_id=record.user_id,
        refresh_token_hash=record.refresh_token_hash,
        previous_refresh_token_hash=record.previous_refresh_token_hash,
        refreshed_at=record.refreshed_at,
        expires_at=record.expires_at,
        last_used_at=record.last_used_at,
        revoked_at=record.revoked_at,
        revoked_reason=SessionRevocationReason(record.revoked_reason) if record.revoked_reason else None,
        user_agent=record.user_agent,
        ip_address=str(record.ip_address) if record.ip_address is not None else None,
        created_at=record.created_at,
    )


class SqlAlchemySessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: NewSession) -> Session:
        record = SessionRecord(
            user_id=session.user_id,
            refresh_token_hash=session.refresh_token_hash,
            expires_at=session.expires_at,
            created_at=session.created_at,
            last_used_at=session.created_at,
            user_agent=session.user_agent,
            ip_address=session.ip_address,
        )
        self._session.add(record)
        await self._session.flush()
        await self._session.refresh(record)
        return to_session(record)

    async def get(self, session_id: uuid.UUID) -> Session | None:
        record = await self._session.get(SessionRecord, session_id, populate_existing=True)
        return to_session(record) if record else None

    async def get_for_update(self, session_id: uuid.UUID) -> Session | None:
        record = await self._session.scalar(
            select(SessionRecord)
            .where(SessionRecord.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return to_session(record) if record else None

    async def rotate(
        self, session_id: uuid.UUID, *, new_hash: bytes, previous_hash: bytes, at: datetime
    ) -> None:
        await self._session.execute(
            update(SessionRecord)
            .where(SessionRecord.id == session_id)
            .values(
                refresh_token_hash=new_hash,
                previous_refresh_token_hash=previous_hash,
                refreshed_at=at,
                last_used_at=at,
            )
        )

    async def revoke(self, session_id: uuid.UUID, *, reason: SessionRevocationReason, at: datetime) -> None:
        await self._session.execute(
            update(SessionRecord)
            .where(SessionRecord.id == session_id, SessionRecord.revoked_at.is_(None))
            .values(revoked_at=at, revoked_reason=reason.value)
        )
