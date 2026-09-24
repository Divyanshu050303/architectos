"""One invitation accepted from two tabs at once: exactly one membership, one success."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.domain.errors import DomainError
from core.domain.identity.tokens import generate_token, hash_token
from core.domain.organizations.invitation_service import InvitationService, InvitationSettings
from persistence.database import create_session_factory
from persistence.models import InvitationRecord, OrganizationMemberRecord, OrganizationRecord, UserRecord
from persistence.repositories.users import to_user
from persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.unit.identity.fakes import RecordingMailer

pytestmark = pytest.mark.integration


async def test_concurrent_acceptance_creates_one_membership(engine: AsyncEngine) -> None:
    sessions = create_session_factory(engine)
    token, now = generate_token(), datetime.now(UTC)
    async with sessions() as db, db.begin():
        org = OrganizationRecord(name="Race Org")
        bob = UserRecord(
            email="race-invite@example.com", name="Bob", password_hash="$argon2id$x", email_verified_at=now
        )
        db.add_all([org, bob])
        await db.flush()
        db.add(
            InvitationRecord(
                organization_id=org.id,
                email=bob.email,
                role="member",
                token_hash=hash_token(token),
                expires_at=now + timedelta(days=1),
                created_at=now,
            )
        )
        await db.flush()
        await db.refresh(bob)
        user = to_user(bob)

    async def accept() -> bool:
        async with sessions() as db:
            service = InvitationService(
                SqlAlchemyUnitOfWork(db), mailer=RecordingMailer(), settings=InvitationSettings()
            )
            try:
                await service.accept(user=user, token=token)
            except DomainError:
                return False
            return True

    try:
        outcomes = await asyncio.gather(*(accept() for _ in range(4)))
        assert sorted(outcomes) == [False, False, False, True]
        async with sessions() as db:
            members = await db.scalar(
                select(func.count()).where(OrganizationMemberRecord.organization_id == org.id)
            )
        assert members == 1
    finally:
        async with sessions() as db, db.begin():
            await db.execute(delete(OrganizationRecord).where(OrganizationRecord.id == org.id))
            await db.execute(delete(UserRecord).where(UserRecord.id == user.id))
