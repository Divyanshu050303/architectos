from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.projects.entities import NewProject
from core.domain.projects.enums import ProjectStatus
from core.domain.projects.errors import ProjectSlugTaken
from core.domain.projects.value_objects import CloudProvider, ProjectSettings
from persistence.models import OrganizationRecord, UserRecord
from persistence.repositories.projects import SqlAlchemyProjectRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def context(db: AsyncSession) -> tuple[OrganizationRecord, UserRecord]:
    org = OrganizationRecord(name="Acme")
    user = UserRecord(email="ada@example.com", name="Ada", password_hash="$argon2id$x")
    db.add_all([org, user])
    await db.flush()
    return org, user


def new(
    org: OrganizationRecord, user: UserRecord, name: str = "Food Delivery", **kwargs: object
) -> NewProject:
    return NewProject.create(organization_id=org.id, created_by_user_id=user.id, name=name, **kwargs)  # type: ignore[arg-type]


async def test_round_trip_including_settings(
    db: AsyncSession, context: tuple[OrganizationRecord, UserRecord]
) -> None:
    org, user = context
    projects = SqlAlchemyProjectRepository(db)
    created = await projects.add(
        new(org, user, settings=ProjectSettings(cloud_provider=CloudProvider.AZURE, currency="EUR"))
    )

    assert (created.slug, created.status, created.organization_id) == (
        "food-delivery",
        ProjectStatus.ACTIVE,
        org.id,
    )
    assert await projects.get_live(created.id) == created
    assert await projects.get_live_for_update(created.id) == created


async def test_duplicate_slug_raises_and_the_transaction_survives(
    db: AsyncSession, context: tuple[OrganizationRecord, UserRecord]
) -> None:
    org, user = context
    projects = SqlAlchemyProjectRepository(db)
    await projects.add(new(org, user))

    with pytest.raises(ProjectSlugTaken):
        await projects.add(new(org, user, name="Food  Delivery"))

    other = await projects.add(new(org, user, name="Payments"))
    assert other.slug == "payments"


async def test_save_persists_lifecycle_and_changes_but_not_ownership(
    db: AsyncSession, context: tuple[OrganizationRecord, UserRecord]
) -> None:
    org, user = context
    projects = SqlAlchemyProjectRepository(db)
    created = await projects.add(new(org, user))
    now = datetime.now(UTC)

    renamed = await projects.save(created.with_changes(name="Orders", description="Order flows"))
    archived = await projects.save(renamed.archive(now))
    deleted = await projects.save(archived.delete(now))

    assert (renamed.name, renamed.slug) == ("Orders", "food-delivery")
    assert archived.status is ProjectStatus.ARCHIVED
    assert deleted.deleted_at is not None
    assert await projects.get_live(created.id) is None  # deleted projects are not "live"
    assert deleted.organization_id == org.id
