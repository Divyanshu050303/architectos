"""Concurrent membership changes cannot leave an organization without an owner.

Real, separately committed transactions; the organization lock serializes them. Test rows are
removed afterwards.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from core.domain.errors import DomainError
from core.domain.organizations.enums import Role
from core.domain.organizations.membership_service import MembershipService
from persistence.database import create_session_factory
from persistence.models import OrganizationMemberRecord, OrganizationRecord, UserRecord
from persistence.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration


@pytest.fixture
async def two_owners(
    engine: AsyncEngine,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], uuid.UUID, list[tuple[uuid.UUID, uuid.UUID]]]]:
    sessions = create_session_factory(engine)
    tag = uuid.uuid4().hex[:8]
    async with sessions() as db, db.begin():
        org = OrganizationRecord(name=f"Race {tag}")
        users = [
            UserRecord(email=f"race-{tag}-{i}@example.com", name="Race", password_hash="$argon2id$x")
            for i in (1, 2)
        ]
        db.add_all([org, *users])
        await db.flush()
        memberships = [
            OrganizationMemberRecord(organization_id=org.id, user_id=u.id, role="owner") for u in users
        ]
        db.add_all(memberships)
        await db.flush()
        owners = [(u.id, m.id) for u, m in zip(users, memberships, strict=True)]
    try:
        yield sessions, org.id, owners
    finally:
        async with sessions() as db, db.begin():
            await db.execute(delete(OrganizationRecord).where(OrganizationRecord.id == org.id))
            await db.execute(delete(UserRecord).where(UserRecord.id.in_([user_id for user_id, _ in owners])))


async def run_concurrently(
    sessions: async_sessionmaker[AsyncSession], *calls: Callable[[MembershipService], Awaitable[object]]
) -> list[bool]:
    async def attempt(call: Callable[[MembershipService], Awaitable[object]]) -> bool:
        async with sessions() as db:
            try:
                await call(MembershipService(SqlAlchemyUnitOfWork(db)))
            except DomainError:
                return False
            return True

    return list(await asyncio.gather(*(attempt(c) for c in calls)))


async def owner_count(sessions: async_sessionmaker[AsyncSession], org_id: uuid.UUID) -> int:
    async with sessions() as db:
        return int(
            await db.scalar(
                select(func.count()).where(
                    OrganizationMemberRecord.organization_id == org_id,
                    OrganizationMemberRecord.role == "owner",
                )
            )
            or 0
        )


async def test_two_owners_demoting_each_other_at_once(
    two_owners: tuple[async_sessionmaker[AsyncSession], uuid.UUID, list[tuple[uuid.UUID, uuid.UUID]]],
) -> None:
    sessions, org, [(a_user, a_member), (b_user, b_member)] = two_owners

    outcomes = await run_concurrently(
        sessions,
        lambda s: s.change_role(
            organization_id=org, actor_user_id=a_user, member_id=b_member, role=Role.ADMIN
        ),
        lambda s: s.change_role(
            organization_id=org, actor_user_id=b_user, member_id=a_member, role=Role.ADMIN
        ),
    )

    assert sorted(outcomes) == [False, True]  # the second sees it is no longer an owner
    assert await owner_count(sessions, org) == 1


async def test_two_owners_leaving_at_once(
    two_owners: tuple[async_sessionmaker[AsyncSession], uuid.UUID, list[tuple[uuid.UUID, uuid.UUID]]],
) -> None:
    sessions, org, [(a_user, a_member), (b_user, b_member)] = two_owners

    outcomes = await run_concurrently(
        sessions,
        lambda s: s.remove(organization_id=org, actor_user_id=a_user, member_id=a_member),
        lambda s: s.remove(organization_id=org, actor_user_id=b_user, member_id=b_member),
    )

    assert sorted(outcomes) == [False, True]
    assert await owner_count(sessions, org) == 1
