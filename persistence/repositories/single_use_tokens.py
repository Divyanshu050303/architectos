"""Repository for emailed single-use tokens. One implementation serves both token tables
(email verification, password reset), which have identical shape and rules."""

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.identity.entities import SingleUseToken
from persistence.models.single_use_token import SingleUseTokenRecord


class SqlAlchemySingleUseTokenRepository:
    def __init__(self, session: AsyncSession, model: type[SingleUseTokenRecord]) -> None:
        self._session = session
        self._model = model

    async def add(
        self, *, user_id: uuid.UUID, token_hash: bytes, expires_at: datetime, created_at: datetime
    ) -> None:
        self._session.add(
            self._model(user_id=user_id, token_hash=token_hash, expires_at=expires_at, created_at=created_at)
        )
        await self._session.flush()

    async def get_by_hash_for_update(self, token_hash: bytes) -> SingleUseToken | None:
        record = await self._session.scalar(
            select(self._model).where(self._model.token_hash == token_hash).with_for_update()
        )
        if record is None:
            return None
        return SingleUseToken(
            id=record.id,
            user_id=record.user_id,
            token_hash=record.token_hash,
            expires_at=record.expires_at,
            consumed_at=record.consumed_at,
            revoked_at=record.revoked_at,
            created_at=record.created_at,
        )

    async def latest_outstanding_created_at(self, user_id: uuid.UUID) -> datetime | None:
        # Served by the partial "outstanding" index on user_id.
        latest: datetime | None = await self._session.scalar(
            select(func.max(self._model.created_at)).where(
                self._model.user_id == user_id,
                self._model.consumed_at.is_(None),
                self._model.revoked_at.is_(None),
            )
        )
        return latest

    async def revoke_outstanding(self, user_id: uuid.UUID, at: datetime) -> None:
        await self._session.execute(
            update(self._model)
            .where(
                self._model.user_id == user_id,
                self._model.consumed_at.is_(None),
                self._model.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )

    async def mark_consumed(self, token_id: uuid.UUID, at: datetime) -> None:
        await self._session.execute(
            update(self._model).where(self._model.id == token_id).values(consumed_at=at)
        )
