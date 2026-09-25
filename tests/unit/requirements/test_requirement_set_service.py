import uuid
from typing import Any

import pytest

from core.domain.identity.entities import NewUser, User
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import PermissionDenied
from core.domain.organizations.organization_service import OrganizationService
from core.domain.projects.entities import Project
from core.domain.projects.errors import ProjectArchived, ProjectNotFound
from core.domain.projects.project_service import ProjectService
from core.domain.requirements.entities import Requirement, RequirementChanges
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.errors import (
    InvalidRequirementSet,
    RequirementSetConflicts,
    RequirementSetNotFound,
)
from core.domain.requirements.planning import content_hash
from core.domain.requirements.requirement_service import RequirementService
from core.domain.requirements.requirement_set_service import RequirementSetService
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
def requirements(uow: FakeUnitOfWork, clock: FakeClock) -> RequirementService:
    return RequirementService(uow, clock=clock)


@pytest.fixture
def sets(uow: FakeUnitOfWork, clock: FakeClock) -> RequirementSetService:
    return RequirementSetService(uow, clock=clock)


class World:
    def __init__(self, ada: User, vic: User, project: Project, other: Project) -> None:
        self.ada, self.vic, self.project, self.other = ada, vic, project, other


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    users = []
    for email in ("ada@example.com", "vic@example.com"):
        user = await uow.users.add(NewUser(email, email[:3], "$argon2id$x"))
        await uow.users.mark_email_verified(user.id, clock.now)
        stored = await uow.users.get(user.id)
        assert stored is not None
        users.append(stored)
    ada, vic = users
    acme = await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")
    await uow.memberships.add(organization_id=acme.organization.id, user_id=vic.id, role=Role.VIEWER)
    projects = ProjectService(uow, clock=clock)
    project = await projects.create(membership=acme.membership, name="Food Delivery")
    other = await projects.create(membership=acme.membership, name="Payments")
    return World(ada, vic, project, other)


async def add(requirements: RequirementService, world: World, **overrides: Any) -> Requirement:
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
    return await requirements.create(**(fields | overrides))


async def test_a_set_pins_requirements_in_force_at_their_current_versions(
    requirements: RequirementService, sets: RequirementSetService, uow: FakeUnitOfWork, world: World
) -> None:
    active = await add(requirements, world)
    await add(requirements, world, status=RequirementStatus.DRAFT, title="Draft")
    satisfied = await add(requirements, world, title="Done")
    satisfied = await requirements.update(
        project_id=world.project.id,
        requirement_id=satisfied.id,
        user_id=world.ada.id,
        expected_version=1,
        changes=RequirementChanges(status=RequirementStatus.SATISFIED),
        change_reason="Load test passed",
    )

    created = await sets.create(project_id=world.project.id, user_id=world.ada.id, name=" Launch  baseline ")

    assert (created.number, created.label, created.name, created.requirement_count) == (
        1,
        "v1",
        "Launch baseline",
        2,
    )
    assert [(i.reference, i.version) for i in created.items] == [
        (active.reference, 1),
        (satisfied.reference, 2),
    ]
    document = uow.requirement_sets.planning_inputs[created.id]
    assert created.content_hash == content_hash(document)
    assert [r["reference"] for r in document["requirements"]] == ["REQ-1", "REQ-3"]
    event = uow.audit.events[-1]
    assert (event.action.value, event.metadata["number"], event.metadata["planning_input_sha256"]) == (
        "requirement_set.created",
        1,
        created.content_hash,
    )


async def test_later_changes_never_alter_a_set(
    requirements: RequirementService, sets: RequirementSetService, uow: FakeUnitOfWork, world: World
) -> None:
    requirement = await add(requirements, world)
    first = await sets.create(project_id=world.project.id, user_id=world.ada.id)
    before = dict(uow.requirement_sets.planning_inputs[first.id])
    await requirements.update(
        project_id=world.project.id,
        requirement_id=requirement.id,
        user_id=world.ada.id,
        expected_version=1,
        changes=RequirementChanges(structured_data=RPS | {"value": "5000"}),
        change_reason="Forecast grew",
    )
    second = await sets.create(project_id=world.project.id, user_id=world.ada.id)

    assert uow.requirement_sets.planning_inputs[first.id] == before
    assert (await sets.get(project_id=world.project.id, set_id=first.id, user_id=world.ada.id)).items[
        0
    ].version == 1
    assert (second.number, second.items[0].version) == (2, 2)
    assert second.content_hash != first.content_hash


async def test_identical_content_gives_identical_hashes(
    requirements: RequirementService, sets: RequirementSetService, world: World
) -> None:
    await add(requirements, world)
    first = await sets.create(project_id=world.project.id, user_id=world.ada.id, name="a")
    second = await sets.create(project_id=world.project.id, user_id=world.ada.id, name="b")
    assert (first.number, second.number) == (1, 2)
    assert first.content_hash == second.content_hash


async def test_explicit_selection(
    requirements: RequirementService, sets: RequirementSetService, world: World
) -> None:
    first = await add(requirements, world)
    await add(requirements, world, title="Other")
    created = await sets.create(project_id=world.project.id, user_id=world.ada.id, requirement_ids=[first.id])
    assert [i.requirement_id for i in created.items] == [first.id]


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("draft", "not_in_force"),
        ("unknown", "not_found"),
        ("other_project", "not_found"),
        ("deleted", "not_found"),
        ("duplicate", "duplicate"),
    ],
)
async def test_what_cannot_be_pinned(
    requirements: RequirementService, sets: RequirementSetService, world: World, setup: str, reason: str
) -> None:
    active = await add(requirements, world)
    ids: list[uuid.UUID]
    match setup:
        case "draft":
            ids = [(await add(requirements, world, status=RequirementStatus.DRAFT)).id]
        case "unknown":
            ids = [uuid.uuid7()]
        case "other_project":
            ids = [(await add(requirements, world, project_id=world.other.id)).id]
        case "deleted":
            await requirements.delete(
                project_id=world.project.id, requirement_id=active.id, user_id=world.ada.id
            )
            ids = [active.id]
        case _:
            ids = [active.id, active.id]
    with pytest.raises(InvalidRequirementSet) as error:
        await sets.create(project_id=world.project.id, user_id=world.ada.id, requirement_ids=ids)
    assert error.value.details["reason"] == reason


async def test_empty_sets_are_refused(sets: RequirementSetService, world: World) -> None:
    with pytest.raises(InvalidRequirementSet) as error:
        await sets.create(project_id=world.project.id, user_id=world.ada.id)
    assert error.value.details == {"field": "requirement_ids", "reason": "nothing_in_force"}
    with pytest.raises(InvalidRequirementSet) as error:
        await sets.create(project_id=world.project.id, user_id=world.ada.id, requirement_ids=[])
    assert error.value.details == {"field": "requirement_ids", "reason": "empty"}


async def test_conflicting_requirements_cannot_form_a_set(
    requirements: RequirementService, sets: RequirementSetService, uow: FakeUnitOfWork, world: World
) -> None:
    await add(requirements, world, structured_data=RPS | {"value": "10000"})
    await add(requirements, world, structured_data=RPS | {"operator": "<=", "value": "5000"})
    with pytest.raises(RequirementSetConflicts) as error:
        await sets.create(project_id=world.project.id, user_id=world.ada.id)
    [conflict] = error.value.details["conflicts"]
    assert (conflict["reason"], conflict["requirements"]) == ("disjoint_bounds", ["REQ-1", "REQ-2"])
    assert uow.requirement_sets.by_id == {}


async def test_authorization_and_archiving(
    requirements: RequirementService,
    sets: RequirementSetService,
    uow: FakeUnitOfWork,
    clock: FakeClock,
    world: World,
) -> None:
    await add(requirements, world)
    created = await sets.create(project_id=world.project.id, user_id=world.ada.id)
    with pytest.raises(PermissionDenied):
        await sets.create(project_id=world.project.id, user_id=world.vic.id)
    assert (
        await sets.get(project_id=world.project.id, set_id=created.id, user_id=world.vic.id)
    ).id == created.id

    stranger = await uow.users.add(NewUser("eve@example.com", "Eve", "$argon2id$x"))
    with pytest.raises(ProjectNotFound):
        await sets.get(project_id=world.project.id, set_id=created.id, user_id=stranger.id)
    with pytest.raises(RequirementSetNotFound):
        await sets.get(project_id=world.other.id, set_id=created.id, user_id=world.ada.id)

    await ProjectService(uow, clock=clock).archive(project_id=world.project.id, user_id=world.ada.id)
    with pytest.raises(ProjectArchived):
        await sets.create(project_id=world.project.id, user_id=world.ada.id)
    assert (await sets.list(project_id=world.project.id, user_id=world.vic.id)).items[0].id == created.id


async def test_listing_is_newest_first_and_paginates(
    requirements: RequirementService, sets: RequirementSetService, world: World
) -> None:
    await add(requirements, world)
    for _ in range(3):
        await sets.create(project_id=world.project.id, user_id=world.ada.id)
    page = await sets.list(project_id=world.project.id, user_id=world.ada.id, limit=2)
    assert [s.number for s in page.items] == [3, 2]
    assert page.next_cursor is not None
    assert all(s.items == () for s in page.items)  # listings carry no items
