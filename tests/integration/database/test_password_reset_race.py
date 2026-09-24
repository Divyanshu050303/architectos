"""Two requests using the same reset link at once: exactly one changes the password."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.domain.identity.errors import InvalidToken
from core.domain.identity.password_service import PasswordService, ResetSettings
from core.domain.identity.passwords import PasswordHasher, PasswordPolicy
from core.domain.identity.tokens import generate_token, hash_token
from persistence.database import create_session_factory
from persistence.models import PasswordResetTokenRecord, UserRecord
from persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.unit.identity.fakes import RecordingMailer

pytestmark = pytest.mark.integration


async def test_concurrent_resets_with_one_link_succeed_once(engine: AsyncEngine) -> None:
    sessions = create_session_factory(engine)
    hasher = PasswordHasher()
    token, now = generate_token(), datetime.now(UTC)
    async with sessions() as db, db.begin():
        user = UserRecord(
            email="race-reset@example.com", name="Race", password_hash=hasher.hash("old password 123")
        )
        db.add(user)
        await db.flush()
        db.add(
            PasswordResetTokenRecord(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=now + timedelta(minutes=30),
                created_at=now,
            )
        )
    user_id = user.id

    async def reset(new_password: str) -> str | None:
        async with sessions() as db:
            service = PasswordService(
                SqlAlchemyUnitOfWork(db),
                hasher=hasher,
                policy=PasswordPolicy(),
                mailer=RecordingMailer(),
                settings=ResetSettings(),
            )
            try:
                await service.reset(token=token, new_password=new_password)
            except InvalidToken:
                return None
            return new_password

    try:
        candidates = [f"racing password number {i}" for i in range(4)]
        winners = [p for p in await asyncio.gather(*(reset(c) for c in candidates)) if p]
        assert len(winners) == 1
        async with sessions() as db:
            stored = await db.scalar(select(UserRecord.password_hash).where(UserRecord.id == user_id))
        assert stored is not None
        assert hasher.verify(stored, winners[0])
    finally:
        async with sessions() as db, db.begin():
            await db.execute(delete(UserRecord).where(UserRecord.id == user_id))
