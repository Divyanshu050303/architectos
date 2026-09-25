import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.requirements.entities import NewRequirement, Requirement, RequirementChanges
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementSource,
    RequirementStatus,
    RequirementType,
)
from core.domain.requirements.queries import RequirementCursor, RequirementQuery
from persistence.models import OrganizationRecord, ProjectRecord, RequirementVersionRecord, UserRecord
from persistence.repositories.requirements import SqlAlchemyRequirementRepository

pytestmark = pytest.mark.integration

RPS: dict[str, Any] = {
    "metric": "requests_per_second",
    "operator": ">=",
    "value": "2000",
    "unit": "requests/second",
}


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


def new(context: Context, project: ProjectRecord | None = None, **overrides: Any) -> NewRequirement:
    fields: dict[str, Any] = {
        "project_id": (project or context.project).id,
        "created_by_user_id": context.user.id,
        "type": RequirementType.CAPACITY,
        "category": "throughput",
        "title": "API throughput",
        "statement": "The API must support 2,000 requests per second.",
        "priority": RequirementPriority.CRITICAL,
        "status": RequirementStatus.ACTIVE,
        "structured_data": RPS,
    }
    return NewRequirement.create(**(fields | overrides))


async def versions(db: AsyncSession, requirement: Requirement) -> list[RequirementVersionRecord]:
    rows = await db.scalars(
        select(RequirementVersionRecord)
        .where(RequirementVersionRecord.requirement_id == requirement.id)
        .order_by(RequirementVersionRecord.version)
    )
    return list(rows)


async def test_add_stores_version_one_and_round_trips(db: AsyncSession, context: Context) -> None:
    repository = SqlAlchemyRequirementRepository(db)
    created = await repository.add(
        new(context, source=RequirementSource.AI, status=RequirementStatus.DRAFT, confidence="0.85")
    )
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # the current version exists

    assert (created.number, created.version, created.reference) == (1, 1, "REQ-1")
    assert created.content.structured_data == RPS
    assert str(created.confidence) == "0.85"
    assert await repository.get(context.project.id, created.id) == created
    [first] = await versions(db, created)
    assert (first.version, first.structured_data, first.created_by_user_id) == (1, RPS, context.user.id)


async def test_numbers_are_per_project_and_never_reused(db: AsyncSession, context: Context) -> None:
    repository = SqlAlchemyRequirementRepository(db)
    first = await repository.add(new(context))
    await repository.save_deleted(first.delete(datetime.now(UTC)))
    second = await repository.add(new(context))
    elsewhere = await repository.add(new(context, context.other))
    assert (second.number, elsewhere.number) == (2, 1)


async def test_save_appends_a_version_and_moves_the_current_state(db: AsyncSession, context: Context) -> None:
    repository = SqlAlchemyRequirementRepository(db)
    created = await repository.add(new(context))
    revision = created.revise(
        expected_version=1,
        changes=RequirementChanges(structured_data=RPS | {"value": "5000"}),
        change_reason="Forecast grew",
        author_user_id=context.user.id,
    )
    assert revision is not None
    saved = await repository.save(revision)
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    assert (saved.version, saved.content.structured_data["value"]) == (2, "5000")
    history = await versions(db, created)
    assert [(v.version, v.structured_data["value"], v.change_reason) for v in history] == [
        (1, "2000", None),
        (2, "5000", "Forecast grew"),
    ]
    listed = await repository.list_versions(context.project.id, created.id, after=None, limit=10)
    assert [v.version for v in listed] == [1, 2]
    assert (await repository.list_versions(context.project.id, created.id, after=1, limit=10))[0].version == 2
    version_one = await repository.get_version(context.project.id, created.id, 1)
    assert version_one is not None
    assert version_one.content.structured_data["value"] == "2000"


async def test_a_stale_revision_is_never_applied(db: AsyncSession, context: Context) -> None:
    repository = SqlAlchemyRequirementRepository(db)
    created = await repository.add(new(context))
    changes = RequirementChanges(title="First writer")
    first = created.revise(
        expected_version=1, changes=changes, change_reason="x", author_user_id=context.user.id
    )
    second = created.revise(
        expected_version=1,
        changes=RequirementChanges(title="Second writer"),
        change_reason="y",
        author_user_id=context.user.id,
    )
    assert first is not None
    assert second is not None
    await repository.save(first)
    with pytest.raises(IntegrityError, match="uq_requirement_versions_requirement_id_version"):
        async with db.begin_nested():
            await repository.save(second)


async def test_everything_is_scoped_by_project(db: AsyncSession, context: Context) -> None:
    repository = SqlAlchemyRequirementRepository(db)
    created = await repository.add(new(context))
    assert await repository.get(context.other.id, created.id) is None
    assert await repository.list_versions(context.other.id, created.id, after=None, limit=10) == []
    assert await repository.get_version(context.other.id, created.id, 1) is None
    assert await repository.list_for_project(context.other.id, RequirementQuery()) == []


async def test_deleted_requirements_are_hidden_but_kept(db: AsyncSession, context: Context) -> None:
    repository = SqlAlchemyRequirementRepository(db)
    created = await repository.add(new(context))
    await repository.save_deleted(created.delete(datetime.now(UTC)))
    assert await repository.get(context.project.id, created.id) is None
    assert await repository.list_for_project(context.project.id, RequirementQuery()) == []
    assert len(await versions(db, created)) == 1


async def test_list_filters_search_and_keyset(db: AsyncSession, context: Context) -> None:
    repository = SqlAlchemyRequirementRepository(db)
    created = [await repository.add(new(context, title=f"Throughput {i}")) for i in range(3)]
    order = await repository.add(
        new(
            context,
            type=RequirementType.FUNCTIONAL,
            category="order",
            title="Place an order",
            statement="Orders with 100% discount_codes apply",
            priority=RequirementPriority.LOW,
            status=RequirementStatus.DRAFT,
            structured_data={},
        )
    )

    def ids(rows: list[Requirement]) -> list[uuid.UUID]:
        return [r.id for r in rows]

    everything = await repository.list_for_project(context.project.id, RequirementQuery())
    assert ids(everything) == [
        r.id for r in sorted([*created, order], key=lambda r: (r.created_at, r.id), reverse=True)
    ]
    filtered = [
        RequirementQuery(type=RequirementType.FUNCTIONAL),
        RequirementQuery(category="order"),
        RequirementQuery(status=RequirementStatus.DRAFT),
        RequirementQuery(priority=RequirementPriority.LOW),
        RequirementQuery(search="PLACE"),
        RequirementQuery(search="100%"),  # wildcards match literally
        RequirementQuery(search="discount_codes"),
    ]
    for query in filtered:
        assert ids(await repository.list_for_project(context.project.id, query)) == [order.id], query
    assert await repository.list_for_project(context.project.id, RequirementQuery(search="1_0")) == []

    page = await repository.list_for_project(context.project.id, RequirementQuery(limit=2))
    cursor = RequirementCursor(created_at=page[-1].created_at, id=page[-1].id)
    rest = await repository.list_for_project(context.project.id, RequirementQuery(after=cursor, limit=10))
    assert ids(page) + ids(rest) == ids(everything)
