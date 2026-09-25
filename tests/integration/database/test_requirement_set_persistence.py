"""Requirement sets in the database: immutability, the same-project guarantee, the repository."""

import uuid
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.requirements.entities import NewRequirement, Requirement
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.planning import content_hash
from core.domain.requirements.requirement_sets import NewRequirementSet, PinnedVersion
from persistence.models import (
    OrganizationRecord,
    ProjectRecord,
    RequirementSetItemRecord,
    RequirementSetRecord,
    UserRecord,
)
from persistence.repositories.requirement_sets import SqlAlchemyRequirementSetRepository
from persistence.repositories.requirements import SqlAlchemyRequirementRepository

pytestmark = pytest.mark.integration

DOCUMENT: dict[str, Any] = {"schema_version": 1, "project": {}, "requirements": [{"reference": "REQ-1"}]}


async def add_in_savepoint(db: AsyncSession, record: object) -> None:
    async with db.begin_nested():
        db.add(record)
        await db.flush()


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


async def requirement(
    db: AsyncSession, context: Context, project: ProjectRecord | None = None
) -> Requirement:
    return await SqlAlchemyRequirementRepository(db).add(
        NewRequirement.create(
            project_id=(project or context.project).id,
            created_by_user_id=context.user.id,
            type=RequirementType.FUNCTIONAL,
            category="order",
            title="Place an order",
            statement="A customer can place an order.",
            priority=RequirementPriority.HIGH,
            status=RequirementStatus.ACTIVE,
        )
    )


def new_set(
    context: Context, *requirements: Requirement, project: ProjectRecord | None = None
) -> NewRequirementSet:
    return NewRequirementSet(
        project_id=(project or context.project).id,
        name="Baseline",
        description="",
        items=tuple(PinnedVersion(r.id, r.number, r.version) for r in requirements),
        schema_version=1,
        planning_input=DOCUMENT,
        content_hash=content_hash(DOCUMENT),
        created_by_user_id=context.user.id,
    )


async def test_round_trip(db: AsyncSession, context: Context) -> None:
    sets = SqlAlchemyRequirementSetRepository(db)
    first = await requirement(db, context)
    created = await sets.add(new_set(context, first))
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    assert (created.number, created.requirement_count, created.content_hash) == (1, 1, content_hash(DOCUMENT))
    loaded = await sets.get(context.project.id, created.id)
    assert loaded == created
    stored = await sets.get_planning_input(context.project.id, created.id)
    assert stored is not None
    summary, document = stored
    assert (summary, document) == (replace(created, items=()), DOCUMENT)
    assert content_hash(document) == created.content_hash
    second = await sets.add(new_set(context, first))
    listed = await sets.list_for_project(context.project.id, before_number=None, limit=10)
    assert [(s.number, s.items) for s in listed] == [(2, ()), (1, ())]
    assert [s.number for s in await sets.list_for_project(context.project.id, before_number=2, limit=10)] == [
        1
    ]
    assert second.number == 2


async def test_sets_are_scoped_by_project(db: AsyncSession, context: Context) -> None:
    sets = SqlAlchemyRequirementSetRepository(db)
    created = await sets.add(new_set(context, await requirement(db, context)))
    assert await sets.get(context.other.id, created.id) is None
    assert await sets.get_planning_input(context.other.id, created.id) is None
    assert await sets.list_for_project(context.other.id, before_number=None, limit=10) == []


async def test_a_set_cannot_pin_another_projects_requirement(db: AsyncSession, context: Context) -> None:
    foreign = await requirement(db, context, context.other)
    with pytest.raises(IntegrityError, match="fk_requirement_set_items_requirement_requirements"):
        async with db.begin_nested():
            await SqlAlchemyRequirementSetRepository(db).add(new_set(context, foreign))


async def test_a_pinned_version_must_exist(db: AsyncSession, context: Context) -> None:
    first = await requirement(db, context)
    ghost = PinnedVersion(first.id, first.number, 7)
    with pytest.raises(IntegrityError, match="fk_requirement_set_items_version_requirement_versions"):
        async with db.begin_nested():
            await SqlAlchemyRequirementSetRepository(db).add(replace(new_set(context, first), items=(ghost,)))


@pytest.mark.parametrize(
    "overrides",
    [{"content_hash": "not-a-hash"}, {"requirement_count": 0}, {"planning_input": []}, {"name": "x" * 101}],
    ids=["hash", "count", "document", "name"],
)
async def test_set_constraints(db: AsyncSession, context: Context, overrides: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "project_id": context.project.id,
        "number": 1,
        "schema_version": 1,
        "planning_input": DOCUMENT,
        "content_hash": content_hash(DOCUMENT),
        "requirement_count": 1,
    }
    with pytest.raises(IntegrityError):
        await add_in_savepoint(db, RequirementSetRecord(**(fields | overrides)))


@pytest.mark.parametrize(
    "statement",
    [
        update(RequirementSetRecord).values(name="Rewritten"),
        delete(RequirementSetRecord),
        update(RequirementSetItemRecord).values(version=2),
        delete(RequirementSetItemRecord),
        text("TRUNCATE requirement_set_items"),
        text("TRUNCATE requirement_sets CASCADE"),
    ],
    ids=["update-set", "delete-set", "update-item", "delete-item", "truncate-items", "truncate-sets"],
)
async def test_sets_are_append_only(db: AsyncSession, context: Context, statement: object) -> None:
    await SqlAlchemyRequirementSetRepository(db).add(new_set(context, await requirement(db, context)))
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.begin_nested():
            await db.execute(statement)  # type: ignore[call-overload]


async def test_unknown_set(db: AsyncSession, context: Context) -> None:
    assert await SqlAlchemyRequirementSetRepository(db).get(context.project.id, uuid.uuid7()) is None
