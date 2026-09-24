import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.audit.entities import AuditAction, AuditCursor, AuditEvent
from core.domain.client import ClientInfo
from core.domain.identity.entities import NewUser
from core.domain.organizations.entities import Membership
from core.domain.organizations.enums import Role
from core.domain.organizations.organization_service import OrganizationService
from persistence.models import AuditLogRecord
from persistence.repositories.audit_logs import SqlAlchemyAuditRepository
from persistence.repositories.organizations import SqlAlchemyMembershipRepository
from persistence.repositories.users import SqlAlchemyUserRepository
from persistence.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

ORG = uuid.uuid7()


async def test_entries_page_newest_first_without_gaps_or_repeats(db: AsyncSession) -> None:
    audit = SqlAlchemyAuditRepository(db, ClientInfo())
    for i in range(7):  # same transaction, so same created_at: the id breaks ties
        await audit.record(
            AuditEvent(AuditAction.MEMBER_INVITED, actor_user_id=None, organization_id=ORG, metadata={"n": i})
        )
    await audit.record(
        AuditEvent(AuditAction.MEMBER_INVITED, actor_user_id=None, organization_id=uuid.uuid7())
    )

    seen: list[int] = []
    cursor: AuditCursor | None = None
    while True:
        page = await audit.list_for_organization(ORG, after=cursor, limit=3)
        seen += [entry.metadata["n"] for entry in page]
        if len(page) < 3:
            break
        cursor = AuditCursor(created_at=page[-1].created_at, id=page[-1].id)

    assert seen == [6, 5, 4, 3, 2, 1, 0]


async def test_request_origin_is_attached(db: AsyncSession) -> None:
    audit = SqlAlchemyAuditRepository(
        db, ClientInfo(user_agent="Test/1.0 " + "x" * 600, ip_address="203.0.113.9")
    )
    await audit.record(AuditEvent(AuditAction.USER_LOGIN, actor_user_id=None, organization_id=ORG))
    [entry] = await audit.list_for_organization(ORG, after=None, limit=10)
    assert entry.ip_address == "203.0.113.9"
    assert entry.user_agent is not None
    assert len(entry.user_agent) == 512


class MembershipWriteFails(SqlAlchemyMembershipRepository):
    async def add(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> Membership:
        raise ConnectionError("lost the database")


async def test_audit_entries_roll_back_with_the_change_they_describe(db: AsyncSession) -> None:
    users = SqlAlchemyUserRepository(db)
    user = await users.add(NewUser("ada@example.com", "Ada", "$argon2id$x"))
    await users.mark_email_verified(user.id, user.created_at)
    verified = await users.get(user.id)
    assert verified is not None
    await db.commit()
    uow = SqlAlchemyUnitOfWork(db)
    uow.memberships = MembershipWriteFails(db)

    with pytest.raises(ConnectionError):
        await OrganizationService(uow).create(user=verified, name="Acme")

    assert await db.scalar(select(func.count()).select_from(AuditLogRecord)) == 0
