"""Architecture CRUD and versioning (Milestone 5): many architectures per project, metadata,
lifecycle, content revisions, restore, concurrency, history, comparison and access."""

import dataclasses
import uuid
from datetime import timedelta
from typing import Any

import pytest

from core.architecture_ir.commands import AddNode, ChangeReplicas, InvalidArchitectureCommand, RenameNode
from core.architecture_ir.component import NodeKind
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.provenance import ProvenanceSource
from core.architecture_ir.traceability import RequirementRef
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.entities import ArchitectureStatus, Position
from core.domain.architecture.errors import (
    ArchitectureArchived,
    ArchitectureNameTaken,
    ArchitectureNotArchived,
    ArchitectureNotFound,
    ArchitectureRevisionNotFound,
    ArchitectureVersionConflict,
    InvalidArchitectureMetadata,
    InvalidLayout,
)
from core.domain.architecture.versions import RevisionSource
from core.domain.audit.entities import AuditAction
from core.domain.errors import NothingToUpdate
from core.domain.organizations.errors import PermissionDenied
from core.domain.pagination import InvalidCursor
from core.domain.projects.errors import ProjectArchived, ProjectNotFound
from core.domain.projects.project_service import ProjectService
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.errors import RequirementSetNotFound
from core.domain.requirements.requirement_service import RequirementService
from tests.unit.architecture_ir.builders import api_and_postgres, node, service_cache_queue
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
        "name": "Orders platform",
        "ir": api_and_postgres(),
    }
    return await service.create(**(fields | overrides))


def ids(world: World, architecture: Any, user: Any = None) -> dict[str, Any]:
    return {
        "project_id": world.project.id,
        "architecture_id": architecture.id,
        "user_id": (user or world.ada).id,
    }


# --- create, read, list ----------------------------------------------------------------------------


async def test_create_stores_metadata_and_revision_one(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World
) -> None:
    architecture, revision = await created(service, world, description="  The main design. ", reason="First")
    assert (architecture.name, architecture.description, architecture.status) == (
        "Orders platform",
        "The main design.",
        ArchitectureStatus.ACTIVE,
    )
    assert (architecture.current_revision, revision.number, revision.parent_number) == (1, 1, None)
    assert revision.ir == api_and_postgres()
    assert revision.snapshot["name"] == "Orders"  # the IR keeps its own title
    [event] = [e for e in uow.audit.events if e.action is AuditAction.ARCHITECTURE_CREATED]
    assert event.resource_id == architecture.id
    assert "Orders" not in str(event.metadata)  # identifiers and counts only


async def test_an_empty_architecture_is_a_valid_start(service: ArchitectureService, world: World) -> None:
    _, revision = await created(service, world, ir=None, description="Draft")
    assert (revision.ir.name, revision.ir.description, revision.ir.nodes) == ("Orders platform", "Draft", ())
    assert revision.summary == "Created with 0 nodes and 0 connections."


async def test_a_project_has_many_architectures_with_unique_live_names(
    service: ArchitectureService, world: World
) -> None:
    first, _ = await created(service, world)
    second, _ = await created(service, world, name="Orders platform v2")
    assert first.id != second.id
    with pytest.raises(ArchitectureNameTaken):
        await created(service, world, name="  orders   PLATFORM ")
    await created(service, world, project_id=world.other.id)  # another project may reuse it
    with pytest.raises(InvalidArchitectureMetadata):
        await created(service, world, name="   ")


async def test_listing_is_scoped_paginated_and_filtered(
    service: ArchitectureService, world: World, clock: FakeClock
) -> None:
    made = []
    for name in ("Alpha", "Beta", "Gamma", "Delta"):
        made.append((await created(service, world, name=name))[0])
        clock.advance(timedelta(seconds=1))
    await created(service, world, project_id=world.other.id, name="Elsewhere")
    await service.archive(**ids(world, made[1]))
    page = await service.list(project_id=world.project.id, user_id=world.vic.id, limit=2)
    assert [a.name for a in page.items] == ["Delta", "Gamma"]  # newest first
    rest = await service.list(project_id=world.project.id, user_id=world.vic.id, cursor=page.next_cursor)
    assert ([a.name for a in rest.items], rest.next_cursor) == (["Beta", "Alpha"], None)
    archived = await service.list(
        project_id=world.project.id, user_id=world.vic.id, status=ArchitectureStatus.ARCHIVED
    )
    assert [a.name for a in archived.items] == ["Beta"]
    found = await service.list(project_id=world.project.id, user_id=world.vic.id, search="  EL ")
    assert [a.name for a in found.items] == ["Delta"]
    with pytest.raises(InvalidCursor):
        await service.list(project_id=world.project.id, user_id=world.vic.id, cursor="bogus")


async def test_read_the_current_architecture(service: ArchitectureService, world: World) -> None:
    architecture, revision = await created(service, world)
    got, current, layout = await service.get(**ids(world, architecture, world.vic))
    assert (got, current, layout.positions) == (architecture, revision, {})
    with pytest.raises(ArchitectureNotFound):
        await service.get(project_id=world.project.id, architecture_id=uuid.uuid4(), user_id=world.ada.id)
    with pytest.raises(ArchitectureNotFound):  # through another project: indistinguishable from missing
        await service.get(project_id=world.other.id, architecture_id=architecture.id, user_id=world.ada.id)


# --- metadata (no revision) ------------------------------------------------------------------------


async def test_metadata_updates_create_no_revision(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World
) -> None:
    architecture, _ = await created(service, world)
    renamed = await service.update_metadata(
        **ids(world, architecture), name="Checkout platform", description="New"
    )
    assert (renamed.name, renamed.description, renamed.current_revision) == ("Checkout platform", "New", 1)
    assert renamed.updated_by_user_id == world.ada.id
    [event] = [e for e in uow.audit.events if e.action is AuditAction.ARCHITECTURE_UPDATED]
    assert event.metadata["changed_fields"] == ["name", "description"]
    assert "Checkout" not in str(event.metadata)
    same = await service.update_metadata(**ids(world, architecture), name="Checkout platform")
    assert same == renamed  # nothing changed, nothing recorded
    assert len([e for e in uow.audit.events if e.action is AuditAction.ARCHITECTURE_UPDATED]) == 1
    with pytest.raises(NothingToUpdate):
        await service.update_metadata(**ids(world, architecture))
    other, _ = await created(service, world, name="Other")
    with pytest.raises(ArchitectureNameTaken):
        await service.update_metadata(**ids(world, other), name="checkout platform")


# --- content revisions -----------------------------------------------------------------------------


async def test_edits_create_revisions_and_history_is_kept(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World, clock: FakeClock
) -> None:
    architecture, _ = await created(service, world)
    revised = await service.edit(
        **ids(world, architecture),
        base_version=1,
        commands=[ChangeReplicas("api", 6), AddNode(node("cache", NodeKind.CACHE, name="Cache"))],
        reason="Black Friday",
    )
    assert revised.created
    second = revised.revision
    assert (second.number, second.parent_number, second.reason, revised.architecture.current_revision) == (
        2,
        1,
        "Black Friday",
        2,
    )
    assert second.summary == revised.changes.summary() == "1 node added (Cache); 1 node modified."
    api = second.ir.node("api")
    assert api is not None
    edit = api.field_provenance["configuration.replicas"]
    assert (edit.source, edit.actor, edit.recorded_at) == (
        ProvenanceSource.USER_EDIT,
        f"user:{world.ada.id}",
        clock.now,
    )
    _, first, _ = await service.version(**ids(world, architecture, world.vic), number=1)
    assert first.ir == api_and_postgres()  # history is untouched
    [event] = [e for e in uow.audit.events if e.action is AuditAction.ARCHITECTURE_REVISED]
    assert (
        event.metadata["revision"],
        event.metadata["elements_added"],
        event.metadata["elements_modified"],
    ) == (2, 1, 1)


async def test_a_whole_content_update(service: ArchitectureService, world: World) -> None:
    architecture, _ = await created(service, world)
    proposal = dataclasses.replace(
        api_and_postgres(), nodes=(*api_and_postgres().nodes, node("q", NodeKind.QUEUE))
    )
    revised = await service.replace(
        **ids(world, architecture),
        base_version=1,
        ir=proposal,
        source=RevisionSource.IMPORT,
        reason="Imported",
    )
    assert (revised.revision.number, revised.revision.source) == (2, RevisionSource.IMPORT)
    assert [c.element_id for c in revised.changes.nodes] == ["q"]


async def test_an_identical_update_creates_no_revision(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World
) -> None:
    architecture, first = await created(service, world)
    audited = len(uow.audit.events)
    same = await service.replace(**ids(world, architecture), base_version=1, ir=api_and_postgres())
    assert (same.created, same.revision, same.changes.is_empty) == (False, first, True)
    noop = await service.edit(
        **ids(world, architecture), base_version=1, commands=[RenameNode("api", "Orders API")]
    )
    assert (noop.created, noop.revision.number) == (False, 1)
    assert len(uow.audit.events) == audited
    history = await service.history(**ids(world, architecture))
    assert [r.number for r in history.page.items] == [1]


async def test_a_stale_update_is_a_conflict_not_last_write_wins(
    service: ArchitectureService, world: World
) -> None:
    architecture, _ = await created(service, world)
    await service.edit(**ids(world, architecture), base_version=1, commands=[ChangeReplicas("api", 2)])
    with pytest.raises(ArchitectureVersionConflict) as raised:
        await service.replace(**ids(world, architecture), base_version=1, ir=service_cache_queue())
    assert raised.value.details == {"latest_version": 2}
    _, current, _ = await service.get(**ids(world, architecture))
    assert current.number == 2


async def test_invalid_content_creates_nothing(service: ArchitectureService, world: World) -> None:
    architecture, _ = await created(service, world)
    with pytest.raises(InvalidArchitectureCommand):
        await service.edit(**ids(world, architecture), base_version=1, commands=[RenameNode("ghost", "x")])
    with pytest.raises(InvalidArchitecture):
        await service.edit(**ids(world, architecture), base_version=1, commands=[ChangeReplicas("api", -1)])
    history = await service.history(**ids(world, architecture))
    assert [r.number for r in history.page.items] == [1]


# --- restore a revision ----------------------------------------------------------------------------


async def test_restoring_a_revision_creates_a_new_one_and_keeps_history(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World
) -> None:
    architecture, first = await created(service, world)
    await service.edit(**ids(world, architecture), base_version=1, commands=[ChangeReplicas("api", 6)])
    await service.edit(
        **ids(world, architecture), base_version=2, commands=[AddNode(node("q", NodeKind.QUEUE))]
    )
    restored = await service.restore_revision(
        **ids(world, architecture), number=1, base_version=3, reason="Roll back"
    )
    fourth = restored.revision
    assert (fourth.number, fourth.parent_number, fourth.restored_from, fourth.reason) == (
        4,
        3,
        1,
        "Roll back",
    )
    assert fourth.ir == first.ir
    assert fourth.content_hash == first.content_hash
    assert fourth.summary.startswith("Restored v1: ")
    history = await service.history(**ids(world, architecture))
    assert [(r.number, r.restored_from) for r in history.page.items] == [
        (4, 1),
        (3, None),
        (2, None),
        (1, None),
    ]
    for number in (1, 2, 3):  # nothing in between was erased or changed
        _, kept, _ = await service.version(**ids(world, architecture), number=number)
        assert kept.number == number
    [event] = [e for e in uow.audit.events if e.action is AuditAction.ARCHITECTURE_REVISION_RESTORED]
    assert (event.metadata["revision"], event.metadata["restored_from"]) == (4, 1)


async def test_restore_rules(service: ArchitectureService, world: World) -> None:
    architecture, _ = await created(service, world)
    await service.edit(**ids(world, architecture), base_version=1, commands=[ChangeReplicas("api", 6)])
    with pytest.raises(ArchitectureVersionConflict):
        await service.restore_revision(**ids(world, architecture), number=1, base_version=1)
    with pytest.raises(ArchitectureRevisionNotFound):
        await service.restore_revision(**ids(world, architecture), number=9, base_version=2)
    same = await service.restore_revision(**ids(world, architecture), number=2, base_version=2)
    assert (same.created, same.revision.number) == (False, 2)  # restoring the current content


# --- history, comparison, layout -------------------------------------------------------------------


async def test_history_is_paginated_newest_first(service: ArchitectureService, world: World) -> None:
    architecture, _ = await created(service, world)
    for version in range(1, 5):
        await service.edit(
            **ids(world, architecture), base_version=version, commands=[ChangeReplicas("api", version + 3)]
        )
    first = await service.history(**ids(world, architecture), limit=2)
    assert [r.number for r in first.page.items] == [5, 4]
    assert first.architecture.current_revision == 5
    rest = await service.history(**ids(world, architecture), cursor=first.page.next_cursor, limit=10)
    assert ([r.number for r in rest.page.items], rest.page.next_cursor) == ([3, 2, 1], None)


async def test_comparing_revisions_of_one_architecture(service: ArchitectureService, world: World) -> None:
    architecture, _ = await created(service, world)
    other, _ = await created(service, world, name="Other")
    await service.edit(**ids(world, architecture), base_version=1, commands=[ChangeReplicas("api", 9)])
    before, after, changes = await service.compare(
        **ids(world, architecture, world.vic), from_number=1, to_number=2
    )
    assert (before.number, after.number) == (1, 2)
    [change] = changes.nodes
    assert next((f.field, f.before, f.after) for f in change.fields) == ("configuration.replicas", 3, 9)
    with pytest.raises(ArchitectureRevisionNotFound):  # revision 2 exists, but not in this architecture
        await service.compare(**ids(world, other), from_number=1, to_number=2)


async def test_layout_never_creates_a_revision(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World
) -> None:
    architecture, _ = await created(service, world)
    audited = len(uow.audit.events)
    layout = await service.save_layout(**ids(world, architecture), positions={"api": Position(10, 20.5)})
    assert layout.positions["api"] == Position(10, 20.5)
    _, revision, stored = await service.get(**ids(world, architecture))
    assert (revision.number, stored.positions) == (1, layout.positions)
    assert len(uow.audit.events) == audited
    with pytest.raises(InvalidLayout):
        await service.save_layout(**ids(world, architecture), positions={"ghost": Position(0, 0)})


# --- lifecycle -------------------------------------------------------------------------------------


async def test_archive_restore_and_delete(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World
) -> None:
    architecture, _ = await created(service, world)
    with pytest.raises(ArchitectureNotArchived):
        await service.delete(**ids(world, architecture))
    archived = await service.archive(**ids(world, architecture))
    assert (archived.status, archived.archived_at is not None) == (ArchitectureStatus.ARCHIVED, True)
    assert await service.archive(**ids(world, architecture)) == archived  # idempotent
    for write in (
        service.edit(**ids(world, architecture), base_version=1, commands=[ChangeReplicas("api", 2)]),
        service.update_metadata(**ids(world, architecture), name="x"),
        service.save_layout(**ids(world, architecture), positions={}),
        service.restore_revision(**ids(world, architecture), number=1, base_version=1),
    ):
        with pytest.raises(ArchitectureArchived):
            await write
    await service.get(**ids(world, architecture))  # still readable
    restored = await service.restore(**ids(world, architecture))
    assert (restored.status, restored.archived_at) == (ArchitectureStatus.ACTIVE, None)
    await service.archive(**ids(world, architecture))
    with pytest.raises(PermissionDenied):  # deleting needs architecture.delete (admins and owners)
        await service.delete(**ids(world, architecture, world.vic))
    await service.delete(**ids(world, architecture))
    with pytest.raises(ArchitectureNotFound):
        await service.get(**ids(world, architecture))
    assert (await service.list(project_id=world.project.id, user_id=world.ada.id)).items == []
    assert (architecture.id, 1) in uow.architectures.revisions  # revisions are kept
    actions = [e.action for e in uow.audit.events if e.resource_id == architecture.id]
    assert actions.count(AuditAction.ARCHITECTURE_ARCHIVED) == 2
    assert AuditAction.ARCHITECTURE_RESTORED in actions
    assert AuditAction.ARCHITECTURE_DELETED in actions
    reborn, _ = await created(service, world)  # the name is free again
    assert reborn.id != architecture.id


# --- traceability and access -----------------------------------------------------------------------


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
        await created(service, world, project_id=world.other.id, ir=good)
    assert raised.value.violations[0].rule == "unknown_requirement"
    with pytest.raises(RequirementSetNotFound):
        await created(service, world, name="With set", requirement_set_id=uuid.uuid4())


async def test_who_may_read_and_change(service: ArchitectureService, world: World) -> None:
    architecture, _ = await created(service, world)
    await service.get(**ids(world, architecture, world.vic))  # a viewer reads
    for write in (
        created(service, world, user_id=world.vic.id, name="Viewer's"),
        service.edit(
            **ids(world, architecture, world.vic), base_version=1, commands=[ChangeReplicas("api", 1)]
        ),
        service.update_metadata(**ids(world, architecture, world.vic), name="x"),
        service.archive(**ids(world, architecture, world.vic)),
        service.restore_revision(**ids(world, architecture, world.vic), number=1, base_version=1),
    ):
        with pytest.raises(PermissionDenied):
            await write
    for attempt in (  # another organization cannot even see the project
        service.get(**ids(world, architecture, world.eve)),
        service.list(project_id=world.project.id, user_id=world.eve.id),
        service.version(**ids(world, architecture, world.eve), number=1),
        service.compare(**ids(world, architecture, world.eve), from_number=1, to_number=1),
    ):
        with pytest.raises(ProjectNotFound):
            await attempt


async def test_archived_projects_freeze_their_architectures(
    service: ArchitectureService, uow: FakeUnitOfWork, world: World, clock: FakeClock
) -> None:
    architecture, _ = await created(service, world)
    await ProjectService(uow, clock=clock).archive(project_id=world.project.id, user_id=world.ada.id)
    for write in (
        service.edit(**ids(world, architecture), base_version=1, commands=[ChangeReplicas("api", 1)]),
        service.update_metadata(**ids(world, architecture), name="x"),
        created(service, world, name="New"),
    ):
        with pytest.raises(ProjectArchived):
            await write
    await service.get(**ids(world, architecture))
