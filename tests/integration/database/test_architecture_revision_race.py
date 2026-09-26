"""Two people edit the same revision at the same moment: exactly one new revision is created,
the other edit is refused as a conflict, and history stays linear. Real transactions, real locks.

It commits, and revisions are append-only (they cannot be cleaned up), so it runs in a database of
its own, dropped afterwards."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.architecture_ir.commands import ChangeReplicas
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.errors import ArchitectureVersionConflict
from persistence.database import create_engine, create_session_factory
from persistence.models import OrganizationMemberRecord, OrganizationRecord, ProjectRecord, UserRecord
from persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.conftest import alembic_config, create_scratch_database, drop_scratch_database
from tests.unit.architecture_ir.builders import api_and_postgres

pytestmark = pytest.mark.integration


@pytest.fixture
def own_database_url() -> Iterator[str]:
    url = create_scratch_database()
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        drop_scratch_database(url)


@pytest.fixture
async def own_engine(own_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(own_database_url)
    yield engine
    await engine.dispose()


async def test_concurrent_edits_of_one_revision_create_one_revision(own_engine: AsyncEngine) -> None:
    sessions = create_session_factory(own_engine)
    suffix = uuid.uuid4().hex[:8]
    async with sessions() as session, session.begin():
        org = OrganizationRecord(name="Race")
        user = UserRecord(
            email=f"race-architecture-{suffix}@example.com", name="Race", password_hash="$argon2id$x"
        )
        session.add_all([org, user])
        await session.flush()
        session.add(OrganizationMemberRecord(organization_id=org.id, user_id=user.id, role="owner"))
        project = ProjectRecord(organization_id=org.id, name="Race", slug=f"race-{suffix}")
        session.add(project)
    project_id, user_id = project.id, user.id

    async with sessions() as db:
        await ArchitectureService(SqlAlchemyUnitOfWork(db)).create(
            project_id=project_id, user_id=user_id, ir=api_and_postgres()
        )

    async def edit(replicas: int) -> object:
        async with sessions() as db:
            try:
                return await ArchitectureService(SqlAlchemyUnitOfWork(db)).edit(
                    project_id=project_id,
                    user_id=user_id,
                    base_version=1,
                    commands=[ChangeReplicas("api", replicas)],
                )
            except ArchitectureVersionConflict as conflict:
                return conflict

    results = await asyncio.gather(*(edit(n) for n in range(4, 9)))
    conflicts = [r for r in results if isinstance(r, ArchitectureVersionConflict)]
    assert len(conflicts) == 4
    assert all(c.details == {"latest_version": 2} for c in conflicts)
    async with sessions() as db:
        numbers = (
            (
                await db.execute(
                    text("SELECT number FROM architecture_revisions WHERE project_id = :p ORDER BY number"),
                    {"p": project_id},
                )
            )
            .scalars()
            .all()
        )
    assert numbers == [1, 2]
