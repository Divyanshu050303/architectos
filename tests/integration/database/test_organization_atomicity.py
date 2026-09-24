"""Creating an organization and its owner membership is one transaction."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.identity.entities import NewUser, User
from core.domain.organizations.entities import Membership
from core.domain.organizations.enums import Role
from core.domain.organizations.organization_service import OrganizationService
from persistence.models import OrganizationMemberRecord, OrganizationRecord
from persistence.repositories.organizations import SqlAlchemyMembershipRepository
from persistence.repositories.users import SqlAlchemyUserRepository
from persistence.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration


class MembershipWriteFails(SqlAlchemyMembershipRepository):
    async def add(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> Membership:
        raise ConnectionError("database went away between the two inserts")


async def verified_user(db: AsyncSession) -> User:
    users = SqlAlchemyUserRepository(db)
    user = await users.add(NewUser("ada@example.com", "Ada", "$argon2id$x"))
    await users.mark_email_verified(user.id, user.created_at)
    verified = await users.get(user.id)
    assert verified is not None
    # End the setup transaction: the unit of work opens its own, as it does per request.
    await db.commit()
    return verified


async def count(db: AsyncSession, model: type[OrganizationRecord] | type[OrganizationMemberRecord]) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def test_organization_is_rolled_back_when_the_owner_membership_fails(db: AsyncSession) -> None:
    user = await verified_user(db)
    uow = SqlAlchemyUnitOfWork(db)
    uow.memberships = MembershipWriteFails(db)

    with pytest.raises(ConnectionError):
        await OrganizationService(uow).create(user=user, name="Acme")

    assert await count(db, OrganizationRecord) == 0
    assert await count(db, OrganizationMemberRecord) == 0


async def test_successful_creation_writes_both_rows(db: AsyncSession) -> None:
    user = await verified_user(db)

    created = await OrganizationService(SqlAlchemyUnitOfWork(db)).create(user=user, name="Acme")

    assert await count(db, OrganizationRecord) == 1
    member = await db.scalar(select(OrganizationMemberRecord))
    assert member is not None
    assert (member.organization_id, member.role) == (created.organization.id, "owner")
