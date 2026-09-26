"""Architecture use cases: create, read, revise, lay out, compare (Architecture IR phase 4)."""

import dataclasses
import uuid
from typing import Any

import pytest

from core.architecture_ir.commands import AddNode, ChangeReplicas, InvalidArchitectureCommand, RenameNode
from core.architecture_ir.component import NodeKind
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.provenance import ProvenanceSource
from core.architecture_ir.traceability import RequirementRef
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.entities import Position
from core.domain.architecture.errors import (
    ArchitectureAlreadyExists,
    ArchitectureNotFound,
    ArchitectureRevisionNotFound,
    ArchitectureUnchanged,
    ArchitectureVersionConflict,
    InvalidLayout,
)
from core.domain.architecture.versions import RevisionSource
from core.domain.audit.entities import AuditAction
from core.domain.organizations.errors import PermissionDenied
from core.domain.pagination import InvalidCursor
from core.domain.projects.errors import ProjectArchived, ProjectNotFound
from core.domain.projects.project_service import ProjectService
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.errors import RequirementSetNotFound
from core.domain.requirements.requirement_service import RequirementService
from tests.unit.architecture_ir.builders import api_and_postgres, node
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork

from .world import World, make_world


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def service(uow: FakeUnitOfWork, clock: FakeClock) -> ArchitectureService:
    return ArchitectureService(uow, clock=clock)


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    return await make_world(uow, clock)


async def created(service: ArchitectureService, world: World, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "project_id": world.project.id,
        "user_id": world.ada.id,
        "ir": api_and_postgres(),
    }
    return await service.create(**(fields | overrides))


# --- create and read -------------------------------------------------------------------------------


async def test_creating_the_architecture_stores_revision_one_and_audits_it(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World
) -> None:
    architecture, revision = await created(service, world, reason="First design.")
    assert (architecture.project_id, architecture.current_revision) == (world.project.id, 1)
    assert (revision.number, revision.parent_number, revision.source) == (1, None, RevisionSource.USER)
    assert revision.ir == api_and_postgres()
    [event] = [e for e in uow.audit.events if e.action is AuditAction.ARCHITECTURE_CREATED]
    assert event.resource_id == architecture.id
    assert event.metadata == {
        "project_id": str(world.project.id),
        "revision": 1,
        "source": "user",
        "node_count": 3,
        "connection_count": 2,
        "content_sha256": revision.content_hash,
    }
    current, current_revision, layout = await service.current(
        project_id=world.project.id, user_id=world.vic.id
    )
    assert (current.id, current_revision.ir, layout.positions) == (architecture.id, revision.ir, {})


async def test_one_architecture_per_project(service: ArchitectureService, world: World) -> None:
    await created(service, world)
    with pytest.raises(ArchitectureAlreadyExists):
        await created(service, world)
    await created(service, world, project_id=world.other.id)  # another project is fine


async def test_no_architecture_yet(service: ArchitectureService, world: World) -> None:
    for read in (
        service.current(project_id=world.project.id, user_id=world.ada.id),
        service.revision(project_id=world.project.id, user_id=world.ada.id, number=1),
        service.history(project_id=world.project.id, user_id=world.ada.id),
    ):
        with pytest.raises(ArchitectureNotFound):
            await read


# --- revisions -------------------------------------------------------------------------------------


async def test_edits_create_revisions_and_history_is_kept(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World, clock: FakeClock
) -> None:
    await created(service, world)
    revised = await service.edit(
        project_id=world.project.id,
        user_id=world.ada.id,
        base_version=1,
        commands=[ChangeReplicas("api", 6), AddNode(node("cache", NodeKind.CACHE, name="Cache"))],
        reason="Black Friday",
    )
    second, changes = revised.revision, revised.changes
    assert revised.architecture.current_revision == 2
    assert (second.number, second.parent_number, second.reason) == (2, 1, "Black Friday")
    assert second.summary == changes.summary() == "1 node added (Cache); 1 node modified."
    api = second.ir.node("api")
    assert api is not None
    edit = api.field_provenance["configuration.replicas"]
    assert (edit.source, edit.actor, edit.recorded_at) == (
        ProvenanceSource.USER_EDIT,
        f"user:{world.ada.id}",
        clock.now,
    )
    cache = second.ir.node("cache")
    assert cache is not None
    assert cache.provenance == edit  # new elements are attributed to the edit too

    first = await service.revision(project_id=world.project.id, user_id=world.vic.id, number=1)
    assert first.ir == api_and_postgres()  # history is untouched
    [event] = [e for e in uow.audit.events if e.action is AuditAction.ARCHITECTURE_REVISED]
    assert (
        event.metadata["revision"],
        event.metadata["elements_added"],
        event.metadata["elements_modified"],
    ) == (2, 1, 1)
    assert "Cache" not in str(event.metadata)  # identifiers and counts only


async def test_a_stale_edit_is_refused_not_merged(service: ArchitectureService, world: World) -> None:
    await created(service, world)
    edit: dict[str, Any] = {"project_id": world.project.id, "user_id": world.ada.id, "base_version": 1}
    await service.edit(**edit, commands=[RenameNode("api", "Checkout API")])
    with pytest.raises(ArchitectureVersionConflict) as raised:
        await service.edit(**edit, commands=[ChangeReplicas("api", 2)])
    assert raised.value.details == {"latest_version": 2}


async def test_edits_that_change_nothing_or_do_not_fit_create_nothing(
    service: ArchitectureService, world: World
) -> None:
    await created(service, world)
    edit: dict[str, Any] = {"project_id": world.project.id, "user_id": world.ada.id, "base_version": 1}
    with pytest.raises(ArchitectureUnchanged):
        await service.edit(**edit, commands=[RenameNode("api", "Orders API")])  # its current name
    with pytest.raises(InvalidArchitectureCommand):
        await service.edit(**edit, commands=[RenameNode("ghost", "x")])
    with pytest.raises(InvalidArchitecture):
        await service.edit(**edit, commands=[ChangeReplicas("api", -1)])
    page = await service.history(project_id=world.project.id, user_id=world.ada.id)
    assert [r.number for r in page.items] == [1]


async def test_replacing_the_whole_architecture(service: ArchitectureService, world: World) -> None:
    await created(service, world)
    proposal = dataclasses.replace(
        api_and_postgres(), nodes=(*api_and_postgres().nodes, node("q", NodeKind.QUEUE))
    )
    replaced = await service.replace(
        project_id=world.project.id,
        user_id=world.ada.id,
        base_version=1,
        ir=proposal,
        source=RevisionSource.AI,
        reason="Approved proposal",
    )
    revision, changes = replaced.revision, replaced.changes
    assert (revision.number, revision.source) == (2, RevisionSource.AI)
    assert [c.element_id for c in changes.nodes] == ["q"]


async def test_history_is_paginated_newest_first(service: ArchitectureService, world: World) -> None:
    await created(service, world)
    for version in range(1, 5):
        await service.edit(
            project_id=world.project.id,
            user_id=world.ada.id,
            base_version=version,
            commands=[ChangeReplicas("api", version + 3)],
        )
    first = await service.history(project_id=world.project.id, user_id=world.ada.id, limit=2)
    assert [r.number for r in first.items] == [5, 4]
    assert first.next_cursor is not None
    rest = await service.history(
        project_id=world.project.id, user_id=world.ada.id, cursor=first.next_cursor, limit=10
    )
    assert ([r.number for r in rest.items], rest.next_cursor) == ([3, 2, 1], None)
    with pytest.raises(InvalidCursor):
        await service.history(project_id=world.project.id, user_id=world.ada.id, cursor="bogus")


async def test_comparing_revisions(service: ArchitectureService, world: World) -> None:
    await created(service, world)
    await service.edit(
        project_id=world.project.id, user_id=world.ada.id, base_version=1, commands=[ChangeReplicas("api", 9)]
    )
    before, after, changes = await service.compare(
        project_id=world.project.id, user_id=world.vic.id, from_number=1, to_number=2
    )
    assert (before.number, after.number) == (1, 2)
    [change] = changes.nodes
    assert next((f.field, f.before, f.after) for f in change.fields) == ("configuration.replicas", 3, 9)
    with pytest.raises(ArchitectureRevisionNotFound):
        await service.compare(project_id=world.project.id, user_id=world.vic.id, from_number=1, to_number=7)


# --- traceability ----------------------------------------------------------------------------------


async def test_requirement_references_must_exist_in_the_project(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World, clock: FakeClock
) -> None:
    requirement = await RequirementService(uow, clock=clock).create(
        project_id=world.project.id,
        user_id=world.ada.id,
        type=RequirementType.CAPACITY,
        category="throughput",
        title="API throughput",
        statement="The API must support 2,000 requests per second.",
        priority=RequirementPriority.CRITICAL,
        status=RequirementStatus.ACTIVE,
        structured_data={
            "metric": "requests_per_second",
            "operator": ">=",
            "value": 2000,
            "unit": "requests/second",
        },
    )
    good = api_and_postgres(requirement_refs=(RequirementRef(requirement.id, 1),))
    await created(service, world, ir=good)
    with pytest.raises(InvalidArchitecture) as raised:
        await created(service, world, project_id=world.other.id, ir=good)  # another project's requirement
    assert raised.value.violations[0].rule == "unknown_requirement"
    with pytest.raises(RequirementSetNotFound):
        await created(service, world, project_id=world.other.id, requirement_set_id=uuid.uuid4())


# --- layout ----------------------------------------------------------------------------------------


async def test_layout_never_creates_a_revision(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World
) -> None:
    await created(service, world)
    audited = len(uow.audit.events)
    layout = await service.save_layout(
        project_id=world.project.id,
        user_id=world.ada.id,
        positions={"api": Position(10, 20.5), "db": Position(-4, 0)},
    )
    assert layout.positions["api"] == Position(10, 20.5)
    _, revision, stored = await service.current(project_id=world.project.id, user_id=world.ada.id)
    assert (revision.number, stored.positions) == (1, layout.positions)
    assert len(uow.audit.events) == audited
    with pytest.raises(InvalidLayout) as raised:
        await service.save_layout(
            project_id=world.project.id, user_id=world.ada.id, positions={"ghost": Position(0, 0)}
        )
    assert raised.value.details == {"reason": "unknown_node", "node_id": "ghost"}
    with pytest.raises(InvalidLayout):
        Position(float("nan"), 0)
    with pytest.raises(InvalidLayout):
        Position(0, 10**7)


# --- access ----------------------------------------------------------------------------------------


async def test_who_may_read_and_change(service: ArchitectureService, world: World) -> None:
    await created(service, world)
    await service.current(project_id=world.project.id, user_id=world.vic.id)  # a viewer reads
    with pytest.raises(PermissionDenied):
        await created(service, world, project_id=world.other.id, user_id=world.vic.id)
    with pytest.raises(PermissionDenied):
        await service.edit(
            project_id=world.project.id,
            user_id=world.vic.id,
            base_version=1,
            commands=[ChangeReplicas("api", 1)],
        )
    with pytest.raises(PermissionDenied):
        await service.save_layout(project_id=world.project.id, user_id=world.vic.id, positions={})
    for attempt in (
        service.current(project_id=world.project.id, user_id=world.eve.id),
        service.revision(project_id=world.project.id, user_id=world.eve.id, number=1),
        service.compare(project_id=world.project.id, user_id=world.eve.id, from_number=1, to_number=1),
    ):
        with pytest.raises(ProjectNotFound):  # another organization cannot even see the project
            await attempt


async def test_archived_projects_are_frozen(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World, clock: FakeClock
) -> None:
    await created(service, world)
    await ProjectService(uow, clock=clock).archive(project_id=world.project.id, user_id=world.ada.id)
    with pytest.raises(ProjectArchived):
        await service.edit(
            project_id=world.project.id,
            user_id=world.ada.id,
            base_version=1,
            commands=[ChangeReplicas("api", 1)],
        )
    with pytest.raises(ProjectArchived):
        await service.save_layout(project_id=world.project.id, user_id=world.ada.id, positions={})
    await service.current(project_id=world.project.id, user_id=world.ada.id)  # still readable
