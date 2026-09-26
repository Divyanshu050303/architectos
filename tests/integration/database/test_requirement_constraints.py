"""Database-level guarantees of requirements and their immutable version history."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import (
    OrganizationRecord,
    ProjectRecord,
    RequirementRecord,
    RequirementVersionRecord,
    UserRecord,
)

pytestmark = pytest.mark.integration

CONTENT: dict[str, object] = {
    "type": "capacity",
    "category": "throughput",
    "title": "API throughput",
    "statement": "The API must support 2,000 requests per second.",
    "priority": "critical",
    "status": "active",
    "source": "user",
    "structured_data": {"metric": "requests_per_second", "operator": ">=", "value": 2000},
}


async def add(db: AsyncSession, *records: object) -> None:
    db.add_all(records)
    await db.flush()


async def add_in_savepoint(db: AsyncSession, records: tuple[object, ...]) -> None:
    async with db.begin_nested():
        db.add_all(records)
        await db.flush()


async def expect_violation(db: AsyncSession, *records: object) -> None:
    with pytest.raises(IntegrityError):
        await add_in_savepoint(db, records)


async def check_deferred(db: AsyncSession) -> None:
    """Deferred constraints are checked at commit; tests roll back, so force the check."""
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def requirement(project_id: uuid.UUID, **overrides: object) -> RequirementRecord:
    return RequirementRecord(
        **(CONTENT | {"project_id": project_id, "number": 1, "current_version": 1} | overrides)
    )


def version(requirement_id: uuid.UUID, number: int = 1, **overrides: object) -> RequirementVersionRecord:
    return RequirementVersionRecord(
        **(CONTENT | {"requirement_id": requirement_id, "version": number} | overrides)
    )


async def with_first_version(db: AsyncSession, record: RequirementRecord) -> RequirementRecord:
    await add(db, record)
    await add(db, version(record.id))
    return record


@pytest.fixture
async def food(db: AsyncSession) -> ProjectRecord:
    org = OrganizationRecord(name="Acme")
    await add(db, org)
    record = ProjectRecord(organization_id=org.id, name="Food Delivery", slug="food-delivery")
    await add(db, record)
    return record


async def test_a_requirement_with_its_first_version(db: AsyncSession, food: ProjectRecord) -> None:
    record = await with_first_version(
        db, requirement(food.id, source="ai", status="draft", confidence=Decimal("0.95"))
    )
    await check_deferred(db)
    await db.refresh(record)
    assert (record.number, record.current_version, record.deleted_at) == (1, 1, None)
    assert record.confidence == Decimal("0.950")
    assert record.id.version == 7


async def test_the_current_version_must_exist(db: AsyncSession, food: ProjectRecord) -> None:
    record = await with_first_version(db, requirement(food.id))
    await db.execute(
        update(RequirementRecord).where(RequirementRecord.id == record.id).values(current_version=2)
    )
    with pytest.raises(IntegrityError, match="current_version"):
        await check_deferred(db)


async def test_a_requirement_needs_an_existing_project(db: AsyncSession) -> None:
    await expect_violation(db, requirement(uuid.uuid7()))


async def test_numbers_are_unique_within_a_project_only(db: AsyncSession, food: ProjectRecord) -> None:
    other = ProjectRecord(organization_id=food.organization_id, name="Payments", slug="payments")
    await add(db, other, requirement(food.id))
    await add(db, requirement(other.id))  # same number, other project: valid
    await expect_violation(db, requirement(food.id))


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "vibes"},
        {"type": "CAPACITY"},
        {"category": ""},
        {"category": "Throughput"},
        {"category": "p95 latency"},
        {"category": "1st"},
        {"category": "x" * 65},
        {"title": ""},
        {"title": "x" * 201},
        {"statement": ""},
        {"statement": "x" * 5001},
        {"priority": "urgent"},
        {"status": "approved"},
        {"source": "email"},
        {"confidence": Decimal("1.001")},
        {"confidence": Decimal("-0.1")},
        {"source": "ai"},
        {"source": "discovery"},
        {"source": "user", "confidence": Decimal("0.9")},
        {"source": "llm"},
        {"scope": "planet"},
        {"scope": "API"},
        {"structured_data": []},
        {"structured_data": "2000"},
        {"structured_data": {"blob": "x" * 16_400}},
        {"number": 0},
        {"current_version": 0},
    ],
    ids=[
        "unknown-type",
        "uppercase-type",
        "empty-category",
        "uppercase-category",
        "category-with-space",
        "category-leading-digit",
        "long-category",
        "empty-title",
        "long-title",
        "empty-statement",
        "long-statement",
        "unknown-priority",
        "unknown-status",
        "unknown-source",
        "confidence-above-one",
        "negative-confidence",
        "ai-without-confidence",
        "discovery-without-confidence",
        "user-with-confidence",
        "unknown-source",
        "unknown-scope",
        "uppercase-scope",
        "structured-data-array",
        "structured-data-string",
        "structured-data-too-large",
        "zero-number",
        "zero-version",
    ],
)
async def test_requirement_field_rules(
    db: AsyncSession, food: ProjectRecord, overrides: dict[str, object]
) -> None:
    await expect_violation(db, requirement(food.id, **overrides))


async def test_ai_requirements_carry_a_confidence(db: AsyncSession, food: ProjectRecord) -> None:
    await with_first_version(db, requirement(food.id, source="ai", confidence=Decimal("0.7"), status="draft"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": 0},
        {"change_reason": ""},
        {"change_reason": "x" * 501},
        {"confidence": Decimal("2")},
        {"structured_data": [1]},
        {"status": "approved"},
    ],
    ids=["zero-version", "empty-reason", "long-reason", "confidence", "structured-data", "status"],
)
async def test_versions_obey_the_same_content_rules(
    db: AsyncSession, food: ProjectRecord, overrides: dict[str, object]
) -> None:
    record = await with_first_version(db, requirement(food.id))
    await expect_violation(db, version(record.id, 2, **overrides))


async def test_version_numbers_are_unique_per_requirement(db: AsyncSession, food: ProjectRecord) -> None:
    record = await with_first_version(db, requirement(food.id))
    await expect_violation(db, version(record.id, 1))
    await add(db, version(record.id, 2, change_reason="Traffic forecast increased from 2K to 5K RPS"))


async def test_a_version_needs_an_existing_requirement(db: AsyncSession) -> None:
    await expect_violation(db, version(uuid.uuid7()))


@pytest.mark.parametrize(
    "statement",
    [
        update(RequirementVersionRecord).values(statement="rewritten"),
        delete(RequirementVersionRecord),
        text("TRUNCATE requirement_versions CASCADE"),
    ],
    ids=["update", "delete", "truncate"],
)
async def test_version_history_is_append_only(
    db: AsyncSession, food: ProjectRecord, statement: object
) -> None:
    await with_first_version(db, requirement(food.id))
    await check_deferred(db)  # no pending foreign-key events, so TRUNCATE reaches the trigger
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.begin_nested():
            await db.execute(statement)  # type: ignore[call-overload]


async def test_requirements_with_history_cannot_be_hard_deleted(
    db: AsyncSession, food: ProjectRecord
) -> None:
    record = await with_first_version(db, requirement(food.id))
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(delete(RequirementRecord).where(RequirementRecord.id == record.id))


async def test_a_project_with_requirements_cannot_be_hard_deleted(
    db: AsyncSession, food: ProjectRecord
) -> None:
    await with_first_version(db, requirement(food.id))
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(delete(ProjectRecord).where(ProjectRecord.id == food.id))


async def test_history_outlives_the_authors_account(db: AsyncSession, food: ProjectRecord) -> None:
    ada = UserRecord(email="ada@example.com", name="Ada", password_hash="$argon2id$x")
    await add(db, ada)
    record = requirement(food.id, created_by_user_id=ada.id)
    await add(db, record)
    await add(db, version(record.id, created_by_user_id=ada.id))

    await db.execute(delete(UserRecord).where(UserRecord.id == ada.id))
    await db.refresh(record)

    assert record.created_by_user_id is None
    kept = await db.scalar(
        select(RequirementVersionRecord.created_by_user_id).where(
            RequirementVersionRecord.requirement_id == record.id
        )
    )
    assert kept == ada.id


@pytest.mark.parametrize(
    "overrides",
    [
        {"scope": "api"},
        {"source": "system", "status": "draft"},
        {"source": "system", "confidence": Decimal("0.8"), "status": "draft"},
        {"source": "imported", "status": "draft"},
        {"source": "discovery", "confidence": Decimal("0.6"), "status": "draft"},
    ],
    ids=["api-scope", "system", "system-with-confidence", "imported", "discovery"],
)
async def test_new_sources_and_scopes(
    db: AsyncSession, food: ProjectRecord, overrides: dict[str, object]
) -> None:
    record = await with_first_version(db, requirement(food.id, **overrides))
    await db.refresh(record)
    assert record.scope == overrides.get("scope", "system")
