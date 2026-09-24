"""Two registrations for the same email at the same moment create exactly one account.

Uses real, separately committed transactions (not the rolled-back test transaction), so the
unique index is what decides the race. Rows are cleaned up afterwards.
"""

import asyncio

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.domain.identity.auth_service import AuthService
from core.domain.identity.passwords import PasswordHasher, PasswordPolicy
from persistence.database import create_session_factory
from persistence.models import UserRecord
from persistence.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

EMAIL = "race@example.com"


async def test_concurrent_duplicate_registrations_create_one_user(engine: AsyncEngine) -> None:
    sessions = create_session_factory(engine)
    hasher, policy = PasswordHasher(), PasswordPolicy()

    async def register(name: str) -> bool:
        async with sessions() as session:
            service = AuthService(SqlAlchemyUnitOfWork(session), hasher=hasher, policy=policy)
            result = await service.register(email=EMAIL, password="correct horse battery staple", name=name)
            return result.user is not None

    try:
        outcomes = await asyncio.gather(*(register(f"Racer {i}") for i in range(5)))
        async with sessions() as session:
            count = await session.scalar(select(func.count()).where(UserRecord.email == EMAIL))
        assert count == 1
        assert sorted(outcomes) == [False, False, False, False, True]
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(UserRecord).where(UserRecord.email == EMAIL))
