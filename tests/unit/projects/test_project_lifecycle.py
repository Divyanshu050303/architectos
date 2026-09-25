import pytest

from core.domain.identity.entities import NewUser, User
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import PermissionDenied
from core.domain.organizations.organization_service import OrganizationService
from core.domain.projects.entities import Project
from core.domain.projects.enums import ProjectStatus
from core.domain.projects.errors import ProjectArchived, ProjectNotArchived, ProjectNotFound
from core.domain.projects.project_service import ProjectService
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


@pytest.fixture
async def setup(
    projects: ProjectService, uow: FakeUnitOfWork, clock: FakeClock
) -> tuple[User, User, Project]:
    ada = await verified(uow, clock, "ada@example.com")
    mel = await verified(uow, clock, "mel@example.com")
    acme = await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")
    await uow.memberships.add(organization_id=acme.organization.id, user_id=mel.id, role=Role.MEMBER)
    project = await projects.create(membership=acme.membership, name="Food Delivery")
    return ada, mel, project


async def test_archive_and_restore_are_idempotent_and_audited_once(
    projects: ProjectService, uow: FakeUnitOfWork, setup: tuple[User, User, Project]
) -> None:
    ada, _, project = setup
    project_id = project.id

    first = await projects.archive(project_id=project_id, user_id=ada.id)
    second = await projects.archive(project_id=project_id, user_id=ada.id)
    assert first.project.status is second.project.status is ProjectStatus.ARCHIVED
    assert first.project.archived_at == second.project.archived_at

    await projects.restore(project_id=project_id, user_id=ada.id)
    await projects.restore(project_id=project_id, user_id=ada.id)

    lifecycle = [a for a in uow.audit.actions() if a in {"project.archived", "project.restored"}]
    assert lifecycle == ["project.archived", "project.restored"]


async def test_members_cannot_archive_restore_or_delete(
    projects: ProjectService, setup: tuple[User, User, Project]
) -> None:
    _, mel, project = setup
    for action in (projects.archive, projects.restore, projects.delete):
        with pytest.raises(PermissionDenied):
            await action(project_id=project.id, user_id=mel.id)


async def test_delete_requires_archiving_then_the_project_is_gone(
    projects: ProjectService, uow: FakeUnitOfWork, setup: tuple[User, User, Project]
) -> None:
    ada, _, project = setup
    project_id = project.id

    with pytest.raises(ProjectNotArchived):
        await projects.delete(project_id=project_id, user_id=ada.id)
    await projects.archive(project_id=project_id, user_id=ada.id)
    await projects.delete(project_id=project_id, user_id=ada.id)

    assert uow.audit.actions()[-1] == "project.deleted"
    for action in (projects.restore, projects.archive, projects.delete):
        with pytest.raises(ProjectNotFound):
            await action(project_id=project_id, user_id=ada.id)
    with pytest.raises(ProjectNotFound):
        await projects.resolve(project_id=project_id, user_id=ada.id)


async def test_archived_projects_reject_edits_until_restored(
    projects: ProjectService, setup: tuple[User, User, Project]
) -> None:
    ada, _, project = setup
    project_id = project.id
    await projects.archive(project_id=project_id, user_id=ada.id)
    with pytest.raises(ProjectArchived):
        await projects.update(project_id=project_id, user_id=ada.id, name="Orders")
    await projects.restore(project_id=project_id, user_id=ada.id)
    assert (
        await projects.update(project_id=project_id, user_id=ada.id, name="Orders")
    ).project.name == "Orders"
