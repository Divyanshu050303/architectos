"""Five refreshes with the same token at the same moment (e.g. five tabs waking up).

The session row is locked during rotation, so exactly one rotates; the others see the token as
"just rotated" and get RefreshConflict. Nobody is signed out.
"""

import asyncio

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.domain.client import ClientInfo
from core.domain.identity.errors import RefreshConflict
from core.domain.identity.passwords import PasswordHasher
from core.domain.identity.session_service import SessionService, SessionSettings
from persistence.database import create_session_factory
from persistence.models import SessionRecord, UserRecord
from persistence.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"


async def test_concurrent_refreshes_rotate_once_and_revoke_nothing(engine: AsyncEngine) -> None:
    sessions = create_session_factory(engine)
    hasher = PasswordHasher()
    async with sessions() as session, session.begin():
        user = UserRecord(email="race-refresh@example.com", name="Race", password_hash=hasher.hash(PASSWORD))
        session.add(user)
    user_id = user.id

    def service(db: object) -> SessionService:
        return SessionService(SqlAlchemyUnitOfWork(db), hasher=hasher, settings=SessionSettings())  # type: ignore[arg-type]

    async with sessions() as db:
        signed_in = await service(db).login(
            email="race-refresh@example.com", password=PASSWORD, client=ClientInfo()
        )

    async def refresh() -> str:
        async with sessions() as db:
            try:
                await service(db).refresh(refresh_token=signed_in.refresh_token)
            except RefreshConflict:
                return "conflict"
            return "rotated"

    try:
        outcomes = await asyncio.gather(*(refresh() for _ in range(5)))
        assert sorted(outcomes) == ["conflict"] * 4 + ["rotated"]
        async with sessions() as db:
            stored = await db.scalar(select(SessionRecord).where(SessionRecord.user_id == user_id))
            assert stored is not None
            assert stored.revoked_at is None
    finally:
        async with sessions() as db, db.begin():
            await db.execute(delete(UserRecord).where(UserRecord.id == user_id))
