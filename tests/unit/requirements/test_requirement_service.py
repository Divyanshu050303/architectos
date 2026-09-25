import uuid
from datetime import timedelta
from typing import Any

import pytest

from core.domain.errors import NothingToUpdate
from core.domain.identity.entities import NewUser, User
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import PermissionDenied
from core.domain.organizations.organization_service import OrganizationService
from core.domain.projects.entities import Project
from core.domain.projects.errors import ProjectArchived, ProjectNotFound
from core.domain.projects.project_service import ProjectService
from core.domain.requirements.entities import Requirement, RequirementChanges
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.errors import RequirementNotFound, RequirementVersionConflict
from core.domain.requirements.queries import RequirementCursor, RequirementQuery
from core.domain.requirements.requirement_service import RequirementService
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork

RPS: dict[str, Any] = {
    "metric": "requests_per_second",
    "operator": ">=",
    "value": "2000",
    "unit": "requests/second",
}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def service(uow: FakeUnitOfWork, clock: FakeClock) -> RequirementService:
    return RequirementService(uow, clock=clock)


async def verified(uow: FakeUnitOfWork, clock: FakeClock, email: str) -> User:
    user = await uow.users.add(NewUser(email, email[:4], "$argon2id$x"))
    await uow.users.mark_email_verified(user.id, clock.now)
    stored = await uow.users.get(user.id)
    assert stored is not None
    return stored


class World:
    def __init__(self, ada: User, vic: User, stranger: User, project: Project, other: Project) -> None:
        self.ada, self.vic, self.stranger, self.project, self.other = ada, vic, stranger, project, other


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    ada = await verified(uow, clock, "ada@example.com")
    vic = await verified(uow, clock, "vic@example.com")
    stranger = await verified(uow, clock, "eve@example.com")
    acme = await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")
    await OrganizationService(uow, clock=clock).create(user=stranger, name="Globex")
    await uow.memberships.add(organization_id=acme.organization.id, user_id=vic.id, role=Role.VIEWER)
    projects = ProjectService(uow, clock=clock)
    project = await projects.create(membership=acme.membership, name="Food Delivery")
    other = await projects.create(membership=acme.membership, name="Payments")
    return World(ada, vic, stranger, project, other)


async def create(service: RequirementService, world: World, **overrides: Any) -> Requirement:
    fields: dict[str, Any] = {
        "project_id": world.project.id,
        "user_id": world.ada.id,
        "type": RequirementType.CAPACITY,
        "category": "throughput",
        "title": "API throughput",
        "statement": "The API must support 2,000 requests per second.",
        "priority": RequirementPriority.CRITICAL,
        "status": RequirementStatus.ACTIVE,
        "structured_data": RPS,
    }
    return await service.create(**(fields | overrides))


async def test_create_numbers_and_audits(
    service: RequirementService, uow: FakeUnitOfWork, world: World
) -> None:
    first = await create(service, world)
    second = await create(service, world, title="Peak throughput")
    elsewhere = await create(service, world, project_id=world.other.id)

    assert [first.reference, second.reference, elsewhere.reference] == ["REQ-1", "REQ-2", "REQ-1"]
    event = uow.audit.events[-1]
    assert (event.action.value, event.resource_id, event.organization_id) == (
        "requirement.created",
        elsewhere.id,
        world.other.organization_id,
    )
    assert event.metadata == {
        "project_id": str(world.other.id),
        "reference": "REQ-1",
        "version": 1,
        "status": "active",
    }


async def test_requirement_text_never_reaches_the_audit_log(
    service: RequirementService, uow: FakeUnitOfWork, world: World
) -> None:
    requirement = await create(service, world, statement="Secret: the vault lives at 10.0.0.7")
    await service.update(
        project_id=world.project.id,
        requirement_id=requirement.id,
        user_id=world.ada.id,
        expected_version=1,
        changes=RequirementChanges(statement="Secret: moved to 10.0.0.8"),
        change_reason="Vault moved",
    )
    assert "10.0.0" not in repr([e.metadata for e in uow.audit.events])
    assert "Vault moved" not in repr([e.metadata for e in uow.audit.events])


async def test_update_appends_a_version_and_audits_what_changed(
    service: RequirementService, uow: FakeUnitOfWork, world: World
) -> None:
    requirement = await create(service, world)
    updated = await service.update(
        project_id=world.project.id,
        requirement_id=requirement.id,
        user_id=world.ada.id,
        expected_version=1,
        changes=RequirementChanges(
            structured_data=RPS | {"value": "5000"}, status=RequirementStatus.SATISFIED
        ),
        change_reason="Traffic forecast increased from 2K to 5K RPS",
    )
    assert updated.version == 2
    versions = uow.requirements.versions[requirement.id]
    assert [v.version for v in versions] == [1, 2]
    assert versions[0].content.structured_data["value"] == "2000"
    assert versions[1].change_reason == "Traffic forecast increased from 2K to 5K RPS"
    assert uow.audit.actions()[-3:] == [
        "requirement.version_created",
        "requirement.updated",
        "requirement.status_changed",
    ]
    assert uow.audit.events[-2].metadata["fields"] == ["structured_data"]
    assert uow.audit.events[-1].metadata.items() >= {"from": "active", "to": "satisfied"}.items()


async def test_an_unchanged_update_writes_nothing(
    service: RequirementService, uow: FakeUnitOfWork, world: World
) -> None:
    requirement = await create(service, world)
    before = len(uow.audit.events)
    same = await service.update(
        project_id=world.project.id,
        requirement_id=requirement.id,
        user_id=world.ada.id,
        expected_version=1,
        changes=RequirementChanges(title="API throughput"),
    )
    assert same.version == 1
    assert len(uow.audit.events) == before
    assert len(uow.requirements.versions[requirement.id]) == 1


async def test_stale_and_empty_updates_are_refused(service: RequirementService, world: World) -> None:
    requirement = await create(service, world, status=RequirementStatus.DRAFT)

    async def update(expected_version: int, changes: RequirementChanges) -> Requirement:
        return await service.update(
            project_id=world.project.id,
            requirement_id=requirement.id,
            user_id=world.ada.id,
            expected_version=expected_version,
            changes=changes,
        )

    await update(1, RequirementChanges(title="v2"))
    with pytest.raises(RequirementVersionConflict):
        await update(1, RequirementChanges(title="v3"))
    with pytest.raises(NothingToUpdate):
        await update(2, RequirementChanges())


async def test_viewers_read_but_cannot_write(service: RequirementService, world: World) -> None:
    requirement = await create(service, world)
    seen = await service.get(project_id=world.project.id, requirement_id=requirement.id, user_id=world.vic.id)
    assert seen.id == requirement.id
    with pytest.raises(PermissionDenied):
        await create(service, world, user_id=world.vic.id)
    with pytest.raises(PermissionDenied):
        await service.update(
            project_id=world.project.id,
            requirement_id=requirement.id,
            user_id=world.vic.id,
            expected_version=1,
            changes=RequirementChanges(priority=RequirementPriority.LOW),
            change_reason="x",
        )
    with pytest.raises(PermissionDenied):
        await service.delete(project_id=world.project.id, requirement_id=requirement.id, user_id=world.vic.id)


async def test_strangers_cannot_tell_the_project_exists(service: RequirementService, world: World) -> None:
    requirement = await create(service, world)
    with pytest.raises(ProjectNotFound):
        await service.get(
            project_id=world.project.id, requirement_id=requirement.id, user_id=world.stranger.id
        )
    with pytest.raises(ProjectNotFound):
        await service.list(project_id=world.project.id, user_id=world.stranger.id, query=RequirementQuery())
    with pytest.raises(ProjectNotFound):
        await create(service, world, user_id=world.stranger.id)


async def test_a_requirement_is_only_found_through_its_own_project(
    service: RequirementService, world: World
) -> None:
    requirement = await create(service, world)
    with pytest.raises(RequirementNotFound):
        await service.get(project_id=world.other.id, requirement_id=requirement.id, user_id=world.ada.id)
    with pytest.raises(RequirementNotFound):
        await service.delete(project_id=world.other.id, requirement_id=requirement.id, user_id=world.ada.id)


async def test_archived_projects_freeze_their_requirements(
    service: RequirementService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    requirement = await create(service, world)
    await ProjectService(uow, clock=clock).archive(project_id=world.project.id, user_id=world.ada.id)

    with pytest.raises(ProjectArchived):
        await create(service, world)
    with pytest.raises(ProjectArchived):
        await service.update(
            project_id=world.project.id,
            requirement_id=requirement.id,
            user_id=world.ada.id,
            expected_version=1,
            changes=RequirementChanges(title="Frozen"),
            change_reason="x",
        )
    with pytest.raises(ProjectArchived):
        await service.delete(project_id=world.project.id, requirement_id=requirement.id, user_id=world.ada.id)
    # Reading still works.
    assert (
        await service.get(project_id=world.project.id, requirement_id=requirement.id, user_id=world.ada.id)
    ).id


async def test_deleted_requirements_disappear_and_their_numbers_are_not_reused(
    service: RequirementService, uow: FakeUnitOfWork, world: World
) -> None:
    first = await create(service, world)
    await service.delete(project_id=world.project.id, requirement_id=first.id, user_id=world.ada.id)
    assert uow.audit.actions()[-1] == "requirement.deleted"
    with pytest.raises(RequirementNotFound):
        await service.get(project_id=world.project.id, requirement_id=first.id, user_id=world.ada.id)
    assert (await create(service, world)).reference == "REQ-2"
    assert len(uow.requirements.versions[first.id]) == 1  # history kept


async def test_list_filters_and_pages(service: RequirementService, clock: FakeClock, world: World) -> None:
    created = []
    for index in range(5):
        clock.advance(timedelta(seconds=1))
        created.append(
            await create(service, world, title=f"Throughput {index}", priority=RequirementPriority.HIGH)
        )
    clock.advance(timedelta(seconds=1))
    await create(
        service,
        world,
        type=RequirementType.FUNCTIONAL,
        category="order",
        title="Place an order",
        status=RequirementStatus.DRAFT,
        structured_data={},
    )

    only_capacity = RequirementQuery(type=RequirementType.CAPACITY, limit=2)
    page = await service.list(project_id=world.project.id, user_id=world.vic.id, query=only_capacity)
    assert [r.content.title for r in page.items] == ["Throughput 4", "Throughput 3"]
    assert page.next_cursor is not None
    rest = await service.list(
        project_id=world.project.id,
        user_id=world.vic.id,
        query=RequirementQuery(
            type=RequirementType.CAPACITY, after=RequirementCursor.decode(page.next_cursor), limit=10
        ),
    )
    assert [r.content.title for r in rest.items] == ["Throughput 2", "Throughput 1", "Throughput 0"]
    assert rest.next_cursor is None

    drafts = await service.list(
        project_id=world.project.id,
        user_id=world.vic.id,
        query=RequirementQuery(status=RequirementStatus.DRAFT),
    )
    assert [r.content.title for r in drafts.items] == ["Place an order"]
    search = await service.list(
        project_id=world.project.id, user_id=world.vic.id, query=RequirementQuery(search="ORDER")
    )
    assert len(search.items) == 1


async def test_unknown_requirement(service: RequirementService, world: World) -> None:
    with pytest.raises(RequirementNotFound):
        await service.get(project_id=world.project.id, requirement_id=uuid.uuid7(), user_id=world.ada.id)
