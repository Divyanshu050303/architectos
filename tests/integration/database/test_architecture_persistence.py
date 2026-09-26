"""Architectures, revisions and layouts in the database (Architecture IR phase 4)."""

import uuid

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.architecture_ir.commands import ChangeReplicas, apply_commands
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.architecture.entities import Architecture, NewArchitecture, Position
from core.domain.architecture.errors import ArchitectureAlreadyExists
from core.domain.architecture.versions import (
    ArchitectureRevision,
    RevisionSource,
    first_revision,
    next_revision,
)
from persistence.models import (
    ArchitectureRecord,
    ArchitectureRevisionRecord,
    OrganizationRecord,
    ProjectRecord,
    RequirementSetRecord,
    UserRecord,
)
from persistence.repositories.architectures import SqlAlchemyArchitectureRepository
from tests.unit.architecture_ir.builders import api_and_postgres, discovered, service_cache_queue

pytestmark = pytest.mark.integration


class Context:
    def __init__(self, user: UserRecord, project: ProjectRecord, other: ProjectRecord) -> None:
        self.user, self.project, self.other = user, project, other


@pytest.fixture
async def context(db: AsyncSession) -> Context:
    org = OrganizationRecord(name="Acme")
    user = UserRecord(email="ada@example.com", name="Ada", password_hash="$argon2id$x")
    db.add_all([org, user])
    await db.flush()
    project = ProjectRecord(organization_id=org.id, name="Food Delivery", slug="food-delivery")
    other = ProjectRecord(organization_id=org.id, name="Payments", slug="payments")
    db.add_all([project, other])
    await db.flush()
    return Context(user, project, other)


async def created(
    db: AsyncSession, project: ProjectRecord, user: UserRecord, ir: ArchitectureIR | None = None
) -> tuple[Architecture, ArchitectureRevision]:
    architecture_id = uuid.uuid7()
    first = first_revision(
        architecture_id, ir or api_and_postgres(), source=RevisionSource.USER, created_by_user_id=user.id
    )
    return await SqlAlchemyArchitectureRepository(db).add(
        NewArchitecture(architecture_id, project.id, user.id), first
    )


@pytest.mark.parametrize("build", [api_and_postgres, service_cache_queue, discovered])
async def test_a_revision_round_trips_exactly(db: AsyncSession, context: Context, build: object) -> None:
    ir = build()  # type: ignore[operator]
    architecture, revision = await created(db, context.project, context.user, ir)
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # the deferred current-revision key holds
    db.expunge_all()
    stored = await SqlAlchemyArchitectureRepository(db).get_revision(context.project.id, 1)
    assert stored == revision
    assert stored is not None
    assert stored.ir == ir
    assert content_hash(stored.ir) == stored.content_hash
    assert await SqlAlchemyArchitectureRepository(db).get(context.project.id) == architecture


async def test_revisions_follow_each_other_and_history_is_kept(db: AsyncSession, context: Context) -> None:
    repository = SqlAlchemyArchitectureRepository(db)
    architecture, first = await created(db, context.project, context.user)
    new, _ = next_revision(
        first,
        apply_commands(first.ir, [ChangeReplicas("api", 8)]),
        source=RevisionSource.AI,
        created_by_user_id=context.user.id,
        reason="Scale out",
    )
    moved, second = await repository.add_revision(architecture, new)
    assert (moved.current_revision, second.number, second.parent_number) == (2, 2, 1)
    assert (await repository.get_revision(context.project.id, 1)) == first
    history = await repository.list_revisions(context.project.id, before=None, limit=10)
    assert [(r.number, r.source) for r in history] == [(2, RevisionSource.AI), (1, RevisionSource.USER)]
    assert await repository.list_revisions(context.project.id, before=2, limit=10) == history[1:]
    assert await repository.get_revision(context.other.id, 1) is None  # scoped by project


async def test_one_architecture_per_project_and_the_transaction_stays_usable(
    db: AsyncSession, context: Context
) -> None:
    await created(db, context.project, context.user)
    with pytest.raises(ArchitectureAlreadyExists):
        await created(db, context.project, context.user)
    await created(db, context.other, context.user)  # still usable


@pytest.mark.parametrize(
    "statement",
    [
        update(ArchitectureRevisionRecord).values(summary="rewritten"),
        delete(ArchitectureRevisionRecord),
        text("TRUNCATE architecture_revisions CASCADE"),
    ],
    ids=["update", "delete", "truncate"],
)
async def test_revisions_are_append_only(db: AsyncSession, context: Context, statement: object) -> None:
    await created(db, context.project, context.user)
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # no deferred check pending
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.begin_nested():
            await db.execute(statement)  # type: ignore[call-overload]


async def add_in_savepoint(db: AsyncSession, record: object) -> None:
    async with db.begin_nested():
        db.add(record)
        await db.flush()


async def execute_in_savepoint(db: AsyncSession, statement: object) -> None:
    async with db.begin_nested():
        await db.execute(statement)  # type: ignore[call-overload]


def revision_row(
    context: Context, architecture_id: uuid.UUID, **overrides: object
) -> ArchitectureRevisionRecord:
    fields: dict[str, object] = {
        "architecture_id": architecture_id,
        "project_id": context.project.id,
        "number": 2,
        "parent_number": 1,
        "ir": to_dict(api_and_postgres()),
        "ir_schema_version": 1,
        "content_hash": "a" * 64,
        "source": "user",
        "summary": "x",
    }
    return ArchitectureRevisionRecord(**(fields | overrides))


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"number": 3, "parent_number": 1}, "parent_is_previous"),
        ({"number": 1, "parent_number": None}, "uq_architecture_revisions_architecture_id_number"),
        ({"source": "magic"}, "source_valid"),
        ({"content_hash": "abc"}, "content_hash_format"),
        ({"summary": ""}, "summary_length"),
        ({"ir": []}, "ir_object"),
        ({"reason": "x" * 501}, "reason_length"),
    ],
)
async def test_revision_constraints(
    db: AsyncSession, context: Context, overrides: dict[str, object], constraint: str
) -> None:
    architecture, _ = await created(db, context.project, context.user)
    row = revision_row(context, architecture.id, **overrides)
    with pytest.raises(IntegrityError, match=constraint):
        await add_in_savepoint(db, row)


async def test_everything_stays_inside_one_project(db: AsyncSession, context: Context) -> None:
    architecture, _ = await created(db, context.project, context.user)
    foreign_set = RequirementSetRecord(
        project_id=context.other.id,
        number=1,
        schema_version=2,
        planning_input={},
        content_hash="a" * 64,
        requirement_count=1,
    )
    db.add(foreign_set)
    await db.flush()
    elsewhere = revision_row(context, architecture.id, project_id=context.other.id)
    with pytest.raises(IntegrityError, match="fk_architecture_revisions_architecture_architectures"):
        await add_in_savepoint(db, elsewhere)
    foreign = revision_row(context, architecture.id, requirement_set_id=foreign_set.id)
    with pytest.raises(IntegrityError, match="fk_architecture_revisions_requirement_set_requirement_sets"):
        await add_in_savepoint(db, foreign)


async def test_the_current_revision_must_exist(db: AsyncSession, context: Context) -> None:
    architecture, _ = await created(db, context.project, context.user)
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    ahead = (
        update(ArchitectureRecord).where(ArchitectureRecord.id == architecture.id).values(current_revision=5)
    )
    with pytest.raises(IntegrityError, match="fk_architectures_current_revision"):
        await execute_in_savepoint(db, ahead)


async def test_layout_is_saved_beside_the_architecture(db: AsyncSession, context: Context) -> None:
    repository = SqlAlchemyArchitectureRepository(db)
    architecture, _ = await created(db, context.project, context.user)
    assert (await repository.get_layout(context.project.id)).positions == {}
    first = await repository.save_layout(architecture, {"api": Position(1, 2.5)}, context.user.id)
    second = await repository.save_layout(architecture, {"db": Position(-3, 4)}, context.user.id)  # replaces
    assert first.positions == {"api": Position(1, 2.5)}
    assert (
        (await repository.get_layout(context.project.id)).positions
        == second.positions
        == {"db": Position(-3, 4)}
    )
    assert (await repository.get_layout(context.other.id)).positions == {}
    assert await db.scalar(select(ArchitectureRecord.current_revision)) == 1  # no revision involved


async def test_an_older_schema_is_upgraded_when_read(
    db: AsyncSession, context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored document is never rewritten; reading it goes through the upgrade chain."""
    await created(db, context.project, context.user)
    seen: list[int] = []

    def upgrade(data, upgrades=None, current=1):  # type: ignore[no-untyped-def]
        seen.append(data["schema_version"])
        return dict(data), data["schema_version"]

    monkeypatch.setattr("core.architecture_ir.serialization.upgrade", upgrade)
    db.expunge_all()
    stored = await SqlAlchemyArchitectureRepository(db).get_revision(context.project.id, 1)
    assert stored is not None
    assert (seen, stored.number) == ([1], 1)
