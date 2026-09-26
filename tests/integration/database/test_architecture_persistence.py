"""Architectures, revisions and layouts in the database (Architecture IR phase 4, Milestone 5)."""

import dataclasses
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.architecture_ir.commands import ChangeReplicas, apply_commands
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.architecture.entities import (
    Architecture,
    ArchitectureQuery,
    ArchitectureStatus,
    NewArchitecture,
    Position,
)
from core.domain.architecture.errors import ArchitectureNameTaken
from core.domain.architecture.versions import (
    ArchitectureRevision,
    RevisionSource,
    first_revision,
    next_revision,
    restored_revision,
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
    db: AsyncSession,
    project: ProjectRecord,
    user: UserRecord,
    ir: ArchitectureIR | None = None,
    name: str = "Orders platform",
) -> tuple[Architecture, ArchitectureRevision]:
    architecture_id = uuid.uuid7()
    first = first_revision(
        architecture_id, ir or api_and_postgres(), source=RevisionSource.USER, created_by_user_id=user.id
    )
    return await SqlAlchemyArchitectureRepository(db).add(
        NewArchitecture(architecture_id, project.id, name, "", user.id), first
    )


async def add_in_savepoint(db: AsyncSession, record: object) -> None:
    async with db.begin_nested():
        db.add(record)
        await db.flush()


async def execute_in_savepoint(db: AsyncSession, statement: object) -> None:
    async with db.begin_nested():
        await db.execute(statement)  # type: ignore[call-overload]


# --- architectures ---------------------------------------------------------------------------------


@pytest.mark.parametrize("build", [api_and_postgres, service_cache_queue, discovered])
async def test_an_architecture_and_its_revision_round_trip_exactly(
    db: AsyncSession, context: Context, build: object
) -> None:
    ir = build()  # type: ignore[operator]
    architecture, revision = await created(db, context.project, context.user, ir)
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # the deferred current-revision key holds
    db.expunge_all()
    repository = SqlAlchemyArchitectureRepository(db)
    assert await repository.get(context.project.id, architecture.id) == architecture
    stored = await repository.get_revision(architecture.id, 1)
    assert stored == revision
    assert stored is not None
    assert (stored.ir, dict(stored.snapshot)) == (ir, to_dict(ir))
    assert content_hash(stored.ir) == stored.content_hash
    assert await repository.get(context.other.id, architecture.id) is None  # scoped by project


async def test_many_architectures_per_project_with_unique_live_names(
    db: AsyncSession, context: Context
) -> None:
    await created(db, context.project, context.user)
    await created(db, context.project, context.user, name="Orders v2")
    with pytest.raises(ArchitectureNameTaken):
        await created(db, context.project, context.user, name="ORDERS PLATFORM")
    await created(db, context.other, context.user)  # the transaction is still usable; other project fine


async def test_listing_is_project_scoped_filtered_and_keyset_paginated(
    db: AsyncSession, context: Context
) -> None:
    repository = SqlAlchemyArchitectureRepository(db)
    names = ["Alpha", "Beta", "Gamma"]
    made = [(await created(db, context.project, context.user, name=n))[0] for n in names]
    await created(db, context.other, context.user, name="Elsewhere")
    archived = made[1].archive(datetime.now(UTC), by=context.user.id)
    await repository.save(archived)
    everything = await repository.list_for_project(context.project.id, ArchitectureQuery())
    assert sorted(a.name for a in everything) == names
    assert everything == sorted(everything, key=lambda a: (a.created_at, a.id), reverse=True)
    first = await repository.list_for_project(context.project.id, ArchitectureQuery(limit=2))
    after = (first[-1].created_at, first[-1].id)
    rest = await repository.list_for_project(context.project.id, ArchitectureQuery(after=after))
    assert [a.id for a in first + rest] == [a.id for a in everything]
    only_archived = await repository.list_for_project(
        context.project.id, ArchitectureQuery(status=ArchitectureStatus.ARCHIVED)
    )
    assert [a.name for a in only_archived] == ["Beta"]
    assert [
        a.name for a in await repository.list_for_project(context.project.id, ArchitectureQuery(search="amm"))
    ] == ["Gamma"]
    literal = await repository.list_for_project(context.project.id, ArchitectureQuery(search="%"))
    assert literal == []  # wildcards are matched literally


async def test_metadata_and_lifecycle_are_saved_and_soft_delete_hides(
    db: AsyncSession, context: Context
) -> None:
    repository = SqlAlchemyArchitectureRepository(db)
    architecture, _ = await created(db, context.project, context.user)
    renamed = await repository.save(
        architecture.with_metadata(name="Checkout", description="d", by=context.user.id)
    )
    assert (renamed.name, renamed.description, renamed.current_revision) == ("Checkout", "d", 1)
    archived = await repository.save(renamed.archive(datetime.now(UTC), by=context.user.id))
    deleted = await repository.save(archived.delete(datetime.now(UTC), by=context.user.id))
    assert deleted.deleted_at is not None
    assert await repository.get(context.project.id, architecture.id) is None
    assert await repository.list_for_project(context.project.id, ArchitectureQuery()) == []
    assert await repository.get_revision(architecture.id, 1) is not None  # revisions are kept
    await created(db, context.project, context.user, name="Checkout")  # the name is free again


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"name": ""}, "name_length"),
        ({"status": "gone"}, "status_valid"),
        ({"status": "archived"}, "archived_at_matches_status"),
        ({"deleted_at": datetime(2026, 1, 1, tzinfo=UTC)}, "deleted_only_when_archived"),
    ],
)
async def test_architecture_constraints(
    db: AsyncSession, context: Context, values: dict[str, object], constraint: str
) -> None:
    architecture, _ = await created(db, context.project, context.user)
    change = update(ArchitectureRecord).where(ArchitectureRecord.id == architecture.id).values(values)
    with pytest.raises(IntegrityError, match=constraint):
        await execute_in_savepoint(db, change)


# --- revisions -------------------------------------------------------------------------------------


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
    restore, _ = restored_revision(second, first, created_by_user_id=context.user.id)
    moved, third = await repository.add_revision(moved, restore)
    assert (moved.current_revision, third.restored_from, third.ir) == (3, 1, first.ir)
    assert await repository.get_revision(architecture.id, 1) == first
    history = await repository.list_revisions(architecture.id, before=None, limit=10)
    assert [(r.number, r.source, r.restored_from) for r in history] == [
        (3, RevisionSource.USER, 1),
        (2, RevisionSource.AI, None),
        (1, RevisionSource.USER, None),
    ]
    assert await repository.list_revisions(architecture.id, before=3, limit=10) == history[1:]


async def test_two_architectures_number_their_revisions_independently(
    db: AsyncSession, context: Context
) -> None:
    repository = SqlAlchemyArchitectureRepository(db)
    one, _ = await created(db, context.project, context.user, name="One")
    two, _ = await created(db, context.project, context.user, name="Two")
    assert (await repository.get_revision(one.id, 1)) is not None
    assert (await repository.get_revision(two.id, 1)) is not None
    assert (await repository.get_revision(two.id, 1)).architecture_id == two.id  # type: ignore[union-attr]


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
        await execute_in_savepoint(db, statement)


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
        ({"restored_from_number": 2}, "restores_an_earlier_revision"),
        ({"restored_from_number": 7, "number": 9, "parent_number": 8}, "fk_architecture_revisions"),
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


async def test_a_failed_write_leaves_nothing_behind(db: AsyncSession, context: Context) -> None:
    """A revision that breaks a constraint rolls back with its savepoint: no revision, and the
    architecture still points at its previous current revision."""
    repository = SqlAlchemyArchitectureRepository(db)
    architecture, first = await created(db, context.project, context.user)
    new, _ = next_revision(
        first,
        apply_commands(first.ir, [ChangeReplicas("api", 2)]),
        source=RevisionSource.USER,
        created_by_user_id=None,
    )
    broken = dataclasses.replace(new, content_hash="not-a-hash")
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await repository.add_revision(architecture, broken)
    assert await repository.get_revision(architecture.id, 2) is None
    assert (await repository.get(context.project.id, architecture.id)).current_revision == 1  # type: ignore[union-attr]


# --- layout and schema versions --------------------------------------------------------------------


async def test_layout_is_saved_beside_the_architecture(db: AsyncSession, context: Context) -> None:
    repository = SqlAlchemyArchitectureRepository(db)
    architecture, _ = await created(db, context.project, context.user)
    other, _ = await created(db, context.project, context.user, name="Other")
    assert (await repository.get_layout(architecture.id)).positions == {}
    first = await repository.save_layout(architecture, {"api": Position(1, 2.5)}, context.user.id)
    second = await repository.save_layout(architecture, {"db": Position(-3, 4)}, context.user.id)  # replaces
    assert first.positions == {"api": Position(1, 2.5)}
    assert (
        (await repository.get_layout(architecture.id)).positions
        == second.positions
        == {"db": Position(-3, 4)}
    )
    assert (await repository.get_layout(other.id)).positions == {}
    assert (
        await db.scalar(
            select(ArchitectureRecord.current_revision).where(ArchitectureRecord.id == architecture.id)
        )
        == 1
    )  # no revision involved


async def test_an_older_schema_is_read_through_upgrades_and_its_snapshot_is_kept(
    db: AsyncSession, context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored document is never rewritten; reading it goes through the upgrade chain, and the
    snapshot keeps the stored form and its schema version."""
    architecture, _ = await created(db, context.project, context.user)
    seen: list[int] = []

    def upgrade(data, upgrades=None, current=1):  # type: ignore[no-untyped-def]
        seen.append(data["schema_version"])
        return dict(data), data["schema_version"]

    monkeypatch.setattr("core.architecture_ir.serialization.upgrade", upgrade)
    db.expunge_all()
    stored = await SqlAlchemyArchitectureRepository(db).get_revision(architecture.id, 1)
    assert stored is not None
    assert (seen, stored.ir_schema_version, stored.snapshot["schema_version"]) == ([1], 1, 1)
