"""Two requests presenting the same verification token at once: exactly one succeeds.

The token row is locked (SELECT ... FOR UPDATE) while it is checked and consumed. Uses real,
separately committed transactions; the test user (and by cascade its token) is removed afterwards.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine

from core.domain.identity.auth_service import AuthService, VerificationSettings
from core.domain.identity.errors import InvalidToken
from core.domain.identity.passwords import PasswordHasher, PasswordPolicy
from core.domain.identity.tokens import generate_token, hash_token
from persistence.database import create_session_factory
from persistence.models import EmailVerificationTokenRecord, UserRecord
from persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.unit.identity.fakes import RecordingMailer

pytestmark = pytest.mark.integration


async def test_concurrent_verification_consumes_the_token_once(engine: AsyncEngine) -> None:
    sessions = create_session_factory(engine)
    token = generate_token()
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        user = UserRecord(email="race-verify@example.com", name="Race", password_hash="$argon2id$x")
        session.add(user)
        await session.flush()
        session.add(
            EmailVerificationTokenRecord(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=now + timedelta(hours=1),
                created_at=now,
            )
        )
        user_id = user.id

    async def verify() -> bool:
        async with sessions() as session:
            service = AuthService(
                SqlAlchemyUnitOfWork(session),
                hasher=PasswordHasher(),
                policy=PasswordPolicy(),
                mailer=RecordingMailer(),
                verification=VerificationSettings(),
            )
            try:
                await service.verify_email(token=token)
            except InvalidToken:
                return False
            return True

    try:
        outcomes = await asyncio.gather(*(verify() for _ in range(5)))
        assert sorted(outcomes) == [False, False, False, False, True]
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(UserRecord).where(UserRecord.id == user_id))
