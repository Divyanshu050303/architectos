import uuid
from datetime import datetime

from sqlalchemy import CursorResult, select, update
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

    async def list_active(self, user_id: uuid.UUID, *, now: datetime, limit: int) -> list[Session]:
        # Uses the partial index ix_sessions_user_id_active (revoked_at IS NULL).
        records = await self._session.scalars(
            select(SessionRecord)
            .where(
                SessionRecord.user_id == user_id,
                SessionRecord.revoked_at.is_(None),
                SessionRecord.expires_at > now,
            )
            .order_by(SessionRecord.last_used_at.desc().nulls_last(), SessionRecord.id.desc())
            .limit(limit)
        )
        return [to_session(record) for record in records]

    async def revoke_owned(
        self, session_id: uuid.UUID, *, user_id: uuid.UUID, reason: SessionRevocationReason, at: datetime
    ) -> bool:
        # Tenant scope is part of the statement itself: another user's id simply matches no row.
        result: CursorResult[tuple[()]] = await self._session.execute(  # type: ignore[assignment]
            update(SessionRecord)
            .where(
                SessionRecord.id == session_id,
                SessionRecord.user_id == user_id,
                SessionRecord.revoked_at.is_(None),
                SessionRecord.expires_at > at,
            )
            .values(revoked_at=at, revoked_reason=reason.value)
        )
        return result.rowcount == 1
