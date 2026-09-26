"""Capacity use cases (Milestone 7, phase 7): running, storing, reading and access."""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.errors import (
    ArchitectureArchived,
    ArchitectureNotFound,
    ArchitectureRevisionNotFound,
)
from core.domain.audit.entities import AuditAction
from core.domain.capacity.capacity_service import CapacityService
from core.domain.capacity.errors import (
    CapacityAnalysisNotFound,
    InvalidCapacityConfig,
    InvalidScenario,
    InvalidWorkload,
)
from core.domain.capacity.queries import BottleneckQuery, ComponentQuery
from core.domain.capacity.results import Certainty, ComponentStatus
from core.domain.capacity.scenarios import Scenario
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.organizations.errors import PermissionDenied
from core.domain.projects.errors import ProjectArchived, ProjectNotFound
from core.domain.projects.project_service import ProjectService
from engines.capacity.service import DeterministicCapacityEngine
from tests.unit.architecture.world import World, make_world
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork

WORKLOAD = WorkloadProfile(
    "Peak", WorkloadType.REQUEST_RESPONSE, peak_rate=Quantity.of("1000", "requests/second")
)
REQUEST = {"kind": ConnectionKind.REQUEST, "protocol": "https", "interaction": Interaction.SYNCHRONOUS}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def service(uow: FakeUnitOfWork, clock: FakeClock) -> CapacityService:
    return CapacityService(uow, DeterministicCapacityEngine(), clock=clock)


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    return await make_world(uow, clock)


def shop() -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            node(
                "api", configuration=Configuration({"replicas": 4, "throughput_per_replica_per_second": 300})
            ),
            node("orders"),
        ),
        connections=(
            connection("web-api", "web", "api", configuration=Configuration({"traffic_ratio": 1}), **REQUEST),
            connection(
                "api-orders", "api", "orders", configuration=Configuration({"traffic_ratio": 1}), **REQUEST
            ),
        ),
    )


async def architecture(uow: FakeUnitOfWork, clock: FakeClock, world: World) -> uuid.UUID:
    created, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Shop", ir=shop()
    )
    return created.id


async def analyze(service: CapacityService, world: World, aid: uuid.UUID, **kwargs: Any) -> Any:
    return await service.analyze(
        project_id=world.project.id,
        architecture_id=aid,
        user_id=kwargs.pop("user_id", world.ada.id),
        workload=kwargs.pop("workload", WORKLOAD),
        **kwargs,
    )


async def test_an_analysis_is_stored_finished_with_its_inputs_and_audited(
    service: CapacityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await analyze(
        service, world, aid, label="Peak check", scenarios=(Scenario("Double", growth=Decimal(2)),)
    )
    a = report.analysis
    assert (a.status, a.revision_number, a.label) == ("partial", 1, "Peak check")  # orders' capacity unknown
    assert report.inputs["workload"]["peak_rate"] == {"value": "1000", "unit": "requests/second"}
    assert report.summary is not None
    assert report.summary.bottlenecks == {"modeled": 0, "candidate": 1}  # orders
    [double] = report.scenarios
    assert ("api", "work_rate", "exceeds_capacity") in double.comparison.new_bottlenecks
    assert [o.required for o in double.scaling if o.node_id == "api"] == [Quantity.of("7", "replicas")]
    event = uow.audit.events[-1]
    assert (event.action, event.resource_id) == (AuditAction.ARCHITECTURE_CAPACITY_ANALYZED, aid)
    assert event.metadata["analysis_id"] == str(a.id)
    assert "orders" not in repr(event.metadata)


async def test_the_same_inputs_give_the_same_result(
    service: CapacityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    first, second = await analyze(service, world, aid), await analyze(service, world, aid)
    assert first.analysis.id != second.analysis.id
    assert first.result_fingerprint == second.result_fingerprint


async def test_invalid_requests_store_nothing(
    service: CapacityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    recorded = len(uow.audit.events)
    for kwargs, error in (
        ({"models": ("no-such-model",)}, InvalidCapacityConfig),
        ({"entries": ("ghost",)}, InvalidCapacityConfig),
        ({"scenarios": (Scenario("x", changes=()), Scenario("x"))}, InvalidWorkload),
        (
            {"scenarios": (Scenario("Launch", target_rate=Quantity.of("5", "events/second")),)},
            InvalidScenario,
        ),
        (
            {
                "workload": WorkloadProfile(
                    "P",
                    WorkloadType.REQUEST_RESPONSE,
                    peak_rate=Quantity.of("1", "requests/second"),
                    requirement_ids=(uuid.uuid4(),),
                )
            },
            InvalidWorkload,
        ),
    ):
        with pytest.raises(error):
            await analyze(service, world, aid, **kwargs)
    assert uow.capacity.reports == {}
    assert len(uow.audit.events) == recorded
    with pytest.raises(ArchitectureRevisionNotFound):
        await analyze(service, world, aid, revision_number=9)


class Broken:
    def analyze(self, *args: Any) -> Any:
        raise RuntimeError("engine bug with internal detail")

    def models(self) -> tuple[dict[str, Any], ...]:
        return ()


async def test_an_engine_failure_is_a_failed_analysis_without_internals(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await analyze(CapacityService(uow, Broken(), clock=clock), world, aid)
    assert report.analysis.status == "failed"
    assert report.analysis.error is not None
    assert (report.analysis.error.code, "internal" in report.analysis.error.message) == (
        "engine_error",
        False,
    )
    assert report.summary is None


async def test_a_storage_failure_is_raised_not_swallowed(
    service: CapacityService,
    uow: FakeUnitOfWork,
    clock: FakeClock,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aid = await architecture(uow, clock, world)

    async def failing(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("database went away")

    monkeypatch.setattr(uow.capacity, "add", failing)
    recorded = len(uow.audit.events)
    with pytest.raises(ConnectionError):
        await analyze(service, world, aid)
    assert len(uow.audit.events) == recorded


async def test_access(service: CapacityService, uow: FakeUnitOfWork, clock: FakeClock, world: World) -> None:
    aid = await architecture(uow, clock, world)
    report = await analyze(service, world, aid)
    with pytest.raises(PermissionDenied):
        await analyze(service, world, aid, user_id=world.vic.id)
    seen = await service.get(
        project_id=world.project.id, architecture_id=aid, analysis_id=report.analysis.id, user_id=world.vic.id
    )
    assert seen.analysis.id == report.analysis.id
    with pytest.raises(ProjectNotFound):
        await service.get(
            project_id=world.project.id,
            architecture_id=aid,
            analysis_id=report.analysis.id,
            user_id=world.eve.id,
        )
    other, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Other"
    )
    with pytest.raises(CapacityAnalysisNotFound):
        await service.get(
            project_id=world.project.id,
            architecture_id=other.id,
            analysis_id=report.analysis.id,
            user_id=world.ada.id,
        )
    with pytest.raises(ArchitectureNotFound):
        await service.get(
            project_id=world.other.id,
            architecture_id=aid,
            analysis_id=report.analysis.id,
            user_id=world.ada.id,
        )


async def test_archived_architectures_and_projects_are_not_analyzed(
    service: CapacityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    architectures = ArchitectureService(uow, clock=clock)
    await architectures.archive(project_id=world.project.id, architecture_id=aid, user_id=world.ada.id)
    with pytest.raises(ArchitectureArchived):
        await analyze(service, world, aid)
    await architectures.restore(project_id=world.project.id, architecture_id=aid, user_id=world.ada.id)
    await ProjectService(uow, clock=clock).archive(project_id=world.project.id, user_id=world.ada.id)
    with pytest.raises(ProjectArchived):
        await analyze(service, world, aid)


async def test_reading_lists_components_and_bottlenecks(
    service: CapacityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    ids = []
    for _ in range(3):
        ids.append((await analyze(service, world, aid)).analysis.id)
        clock.advance(timedelta(seconds=1))
    page = await service.list_analyses(
        project_id=world.project.id, architecture_id=aid, user_id=world.ada.id, limit=2
    )
    rest = await service.list_analyses(
        project_id=world.project.id,
        architecture_id=aid,
        user_id=world.ada.id,
        cursor=page.next_cursor,
        limit=2,
    )
    assert [r.analysis.id for r in (*page.items, *rest.items)] == ids[::-1]

    common = {
        "project_id": world.project.id,
        "architecture_id": aid,
        "analysis_id": ids[0],
        "user_id": world.ada.id,
    }
    first = await service.list_components(**common, query=ComponentQuery(limit=1))
    second = await service.list_components(**common, query=ComponentQuery(limit=5), cursor=first.next_cursor)
    assert [c.node_id for c in (*first.items, *second.items)] == ["api", "orders"]
    estimated = await service.list_components(**common, query=ComponentQuery(ComponentStatus.ESTIMATED))
    assert [c.node_id for c in estimated.items] == ["api"]
    candidates = await service.list_bottlenecks(**common, query=BottleneckQuery(Certainty.CANDIDATE))
    assert [b.node_id for b in candidates] == ["orders"]
    assert await service.list_bottlenecks(**common, query=BottleneckQuery(Certainty.MODELED)) == []


def test_the_model_catalog(uow: FakeUnitOfWork) -> None:
    models = CapacityService(uow, DeterministicCapacityEngine()).models()
    assert [m["id"] for m in models] == sorted(m["id"] for m in models)
    assert all(
        {"id", "version", "kinds", "configuration", "workload", "limitations"} <= set(m) for m in models
    )


async def test_an_archive_during_the_calculation_refuses_the_store(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    """The engine runs outside any transaction; the store re-checks, so a project archived in the
    meantime keeps its analyses frozen."""
    aid = await architecture(uow, clock, world)
    real = DeterministicCapacityEngine()

    class ArchivingMeanwhile:
        def analyze(self, *args: Any) -> Any:
            project = uow.projects.by_id[world.project.id]
            uow.projects.by_id[world.project.id] = project.archive(clock.now)
            return real.analyze(*args)

        def models(self) -> Any:
            return real.models()

    recorded = len(uow.audit.events)
    with pytest.raises(ProjectArchived):
        await analyze(CapacityService(uow, ArchivingMeanwhile(), clock=clock), world, aid)
    assert uow.capacity.reports == {}
    assert len(uow.audit.events) == recorded
