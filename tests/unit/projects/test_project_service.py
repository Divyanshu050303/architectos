import pytest

from core.domain.errors import NothingToUpdate
from core.domain.identity.entities import NewUser, User
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import PermissionDenied
from core.domain.organizations.organization_service import OrganizationService
from core.domain.projects.errors import ProjectArchived, ProjectNotFound, ProjectSlugTaken
from core.domain.projects.project_service import ProjectService
from core.domain.projects.queries import ProjectCursor, ProjectQuery, ProjectSort
from core.domain.projects.value_objects import CloudProvider, ProjectSettings
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def projects(uow: FakeUnitOfWork, clock: FakeClock) -> ProjectService:
    return ProjectService(uow, clock=clock)


async def verified(uow: FakeUnitOfWork, clock: FakeClock, email: str) -> User:
    user = await uow.users.add(NewUser(email, email[:4], "$argon2id$x"))
    await uow.users.mark_email_verified(user.id, clock.now)
    stored = await uow.users.get(user.id)
    assert stored is not None
    return stored


async def org_of(uow: FakeUnitOfWork, clock: FakeClock, owner: User):  # type: ignore[no-untyped-def]
    return await OrganizationService(uow, clock=clock).create(user=owner, name="Acme")


async def test_create_is_audited_and_derives_the_slug(
    projects: ProjectService, uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, "ada@example.com")
    acme = await org_of(uow, clock, ada)

    project = await projects.create(membership=acme.membership, name="Food Delivery Platform")

    assert (project.slug, project.organization_id, project.created_by_user_id) == (
        "food-delivery-platform",
        acme.organization.id,
        ada.id,
    )
    event = uow.audit.events[-1]
    assert (event.action.value, event.resource_id) == ("project.created", project.id)


async def test_duplicate_slugs_are_refused(
    projects: ProjectService, uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, "ada@example.com")
    acme = await org_of(uow, clock, ada)
    await projects.create(membership=acme.membership, name="Food Delivery")
    with pytest.raises(ProjectSlugTaken):
        await projects.create(membership=acme.membership, name="Other", slug="food-delivery")


async def test_viewers_cannot_create_or_update(
    projects: ProjectService, uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, "ada@example.com")
    vic = await verified(uow, clock, "vic@example.com")
    acme = await org_of(uow, clock, ada)
    viewer = await uow.memberships.add(organization_id=acme.organization.id, user_id=vic.id, role=Role.VIEWER)
    project = await projects.create(membership=acme.membership, name="Food Delivery")

    with pytest.raises(PermissionDenied):
        await projects.create(membership=viewer, name="Mine")
    with pytest.raises(PermissionDenied):
        await projects.update(project_id=project.id, user_id=vic.id, name="Hijacked")
    assert (await projects.resolve(project_id=project.id, user_id=vic.id)).membership.role is Role.VIEWER


async def test_other_tenants_see_nothing(
    projects: ProjectService, uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, "ada@example.com")
    grace = await verified(uow, clock, "grace@example.com")
    acme = await org_of(uow, clock, ada)
    project = await projects.create(membership=acme.membership, name="Food Delivery")

    with pytest.raises(ProjectNotFound):
        await projects.resolve(project_id=project.id, user_id=grace.id)
    with pytest.raises(ProjectNotFound):
        await projects.update(project_id=project.id, user_id=grace.id, name="Hijacked")


async def test_update_changes_only_allowed_fields_and_is_audited(
    projects: ProjectService, uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, "ada@example.com")
    acme = await org_of(uow, clock, ada)
    project = await projects.create(membership=acme.membership, name="Food Delivery")

    access = await projects.update(
        project_id=project.id,
        user_id=ada.id,
        name="Orders",
        settings=ProjectSettings(cloud_provider=CloudProvider.AWS),
    )

    assert (access.project.name, access.project.slug) == ("Orders", "food-delivery")
    assert uow.audit.events[-1].metadata == {"fields": ["name", "settings"]}
    with pytest.raises(NothingToUpdate):
        await projects.update(project_id=project.id, user_id=ada.id)


async def test_archived_projects_cannot_be_updated(
    projects: ProjectService, uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, "ada@example.com")
    acme = await org_of(uow, clock, ada)
    project = await projects.create(membership=acme.membership, name="Food Delivery")
    await uow.projects.save(project.archive(clock.now))

    with pytest.raises(ProjectArchived):
        await projects.update(project_id=project.id, user_id=ada.id, name="Orders")


async def test_listing_pages_through_every_project_once(
    projects: ProjectService, uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, "ada@example.com")
    acme = await org_of(uow, clock, ada)
    for i in range(7):
        await projects.create(membership=acme.membership, name=f"Project {i}")

    seen: list[str] = []
    cursor = None
    while True:
        page = await projects.list(
            membership=acme.membership,
            query=ProjectQuery(
                sort=ProjectSort.NAME,
                after=ProjectCursor.decode(cursor, sort=ProjectSort.NAME) if cursor else None,
                limit=3,
            ),
        )
        seen += [p.name for p in page.items]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == [f"Project {i}" for i in range(7)]
