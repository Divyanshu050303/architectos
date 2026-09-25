"""Database-level guarantees of the projects table: tenancy, slug rules, lifecycle states."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import OrganizationRecord, ProjectRecord, UserRecord

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


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


def project(org_id: uuid.UUID, **overrides: object) -> ProjectRecord:
    fields: dict[str, object] = {"organization_id": org_id, "name": "Food Delivery", "slug": "food-delivery"}
    return ProjectRecord(**(fields | overrides))


@pytest.fixture
async def acme(db: AsyncSession) -> OrganizationRecord:
    org = OrganizationRecord(name="Acme")
    await add(db, org)
    return org


async def test_defaults(db: AsyncSession, acme: OrganizationRecord) -> None:
    record = project(acme.id)
    await add(db, record)
    await db.refresh(record)
    assert (record.status, record.description, record.settings, record.archived_at) == (
        "active",
        "",
        {},
        None,
    )
    assert record.id.version == 7


async def test_project_must_belong_to_an_existing_organization(db: AsyncSession) -> None:
    await expect_violation(db, project(uuid.uuid7()))


async def test_slug_is_unique_within_an_organization_only(db: AsyncSession, acme: OrganizationRecord) -> None:
    globex = OrganizationRecord(name="Globex")
    await add(db, globex, project(acme.id))
    await add(db, project(globex.id))  # same slug, other organization: valid
    await expect_violation(db, project(acme.id))


async def test_a_deleted_projects_slug_can_be_reused(db: AsyncSession, acme: OrganizationRecord) -> None:
    await add(db, project(acme.id, status="archived", archived_at=NOW, deleted_at=NOW))
    await add(db, project(acme.id))


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "Food-Delivery",
        "food delivery",
        "-food",
        "food-",
        "food--delivery",
        "food_delivery",
        "a" * 64,
        "fööd",
    ],
)
async def test_slug_format(db: AsyncSession, acme: OrganizationRecord, slug: str) -> None:
    await expect_violation(db, project(acme.id, slug=slug))


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "x" * 101},
        {"description": "x" * 2001},
        {"status": "paused"},
        {"settings": []},
        {"settings": "text"},
    ],
    ids=[
        "empty-name",
        "long-name",
        "long-description",
        "unknown-status",
        "settings-array",
        "settings-string",
    ],
)
async def test_field_rules(db: AsyncSession, acme: OrganizationRecord, overrides: dict[str, object]) -> None:
    await expect_violation(db, project(acme.id, **overrides))


async def test_archived_state_needs_its_timestamp_and_vice_versa(
    db: AsyncSession, acme: OrganizationRecord
) -> None:
    await expect_violation(db, project(acme.id, status="archived"))
    await expect_violation(db, project(acme.id, archived_at=NOW))
    await add(db, project(acme.id, status="archived", archived_at=NOW))


async def test_deletion_requires_the_archived_state(db: AsyncSession, acme: OrganizationRecord) -> None:
    await expect_violation(db, project(acme.id, deleted_at=NOW))


async def test_an_organization_owning_projects_cannot_be_hard_deleted(
    db: AsyncSession, acme: OrganizationRecord
) -> None:
    await add(db, project(acme.id, status="archived", archived_at=NOW, deleted_at=NOW))
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(delete(OrganizationRecord).where(OrganizationRecord.id == acme.id))


async def test_projects_outlive_their_creators_account(db: AsyncSession, acme: OrganizationRecord) -> None:
    ada = UserRecord(email="ada@example.com", name="Ada", password_hash="$argon2id$x")
    await add(db, ada)
    record = project(acme.id, created_by_user_id=ada.id)
    await add(db, record)

    await db.execute(delete(UserRecord).where(UserRecord.id == ada.id))
    await db.refresh(record)

    assert record.created_by_user_id is None


async def test_organization_ownership_is_enforced_by_the_foreign_key(
    db: AsyncSession, acme: OrganizationRecord
) -> None:
    record = project(acme.id)
    await add(db, record)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                update(ProjectRecord)
                .where(ProjectRecord.id == record.id)
                .values(organization_id=uuid.uuid7())
            )
    assert (
        await db.scalar(select(ProjectRecord.organization_id).where(ProjectRecord.id == record.id))
    ) == acme.id
