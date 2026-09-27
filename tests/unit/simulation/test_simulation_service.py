"""Simulation use cases (Milestone 12, phase 10): running, storing, reading, comparing and access."""

import logging
import threading
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.errors import (
    ArchitectureArchived,
    ArchitectureNotFound,
    ArchitectureRevisionNotFound,
)
from core.domain.audit.entities import AuditAction
from core.domain.cost.errors import PricingSnapshotNotFound
from core.domain.organizations.errors import PermissionDenied
from core.domain.projects.errors import ProjectArchived, ProjectNotFound
from core.domain.projects.project_service import ProjectService
from core.domain.simulations.entities import PricingInputs
from core.domain.simulations.errors import InvalidSimulationRequest, SimulationNotFound
from core.domain.simulations.queries import SimulationComponentQuery, SimulationDeltaQuery
from core.domain.simulations.scenarios import ConfigurationChange, Failure, Scenario, WorkloadChange
from core.domain.simulations.simulation_service import SimulationService
from core.domain.simulations.values import AnalysisKind, FailureKind, Impact, RunState
from engines.simulation.service import DeterministicSimulationEngine
from tests.unit.architecture.world import World, make_world
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork
from tests.unit.simulation.test_simulation_capacity import WORKLOAD, shop

A = AnalysisKind
DOUBLE = Scenario("Double", workload=WorkloadChange(growth=Decimal(2)))
OUTAGE = Scenario("Db down", failures=(Failure(FailureKind.COMPONENT, "db"),))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def service(uow: FakeUnitOfWork, clock: FakeClock) -> SimulationService:
    return SimulationService(uow, DeterministicSimulationEngine(), clock=clock)


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    return await make_world(uow, clock)


async def architecture(uow: FakeUnitOfWork, clock: FakeClock, world: World) -> uuid.UUID:
    created, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Shop", ir=shop()
    )
    return created.id


async def simulate(service: SimulationService, world: World, aid: uuid.UUID, **kwargs: Any) -> Any:
    kwargs.setdefault("scenario", DOUBLE)
    return await service.simulate(
        project_id=world.project.id,
        architecture_id=aid,
        user_id=kwargs.pop("user_id", world.ada.id),
        **kwargs,
    )


async def test_a_simulation_is_stored_with_its_inputs_and_audited(
    service: SimulationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await simulate(service, world, aid, workload=WORKLOAD, label="Peak")
    s = report.simulation
    assert (s.status, s.revision_number, s.label) == ("partial", 1, "Peak")  # cost did not run
    assert {r.analysis: r.state for r in report.runs} == {
        A.CAPACITY: RunState.COMPLETED,
        A.COST: RunState.UNSUPPORTED,  # no pricing inputs: not estimated
    }
    stored = uow.simulations.deltas[s.id]
    assert stored
    assert all(d.analysis is A.CAPACITY for d in stored)
    assert report.inputs["scenario"]["name"] == "Double"
    assert report.inputs["requirements"] == []
    assert report.overlay is not None
    event = uow.audit.events[-1]
    assert (event.action, event.resource_id) == (AuditAction.ARCHITECTURE_SIMULATED, aid)
    assert event.metadata["deltas"] == len(stored)
    # the stored rows rebuild the very result that was fingerprinted
    rebuilt = report.result(uow.simulations.components[s.id], stored)
    assert rebuilt.fingerprint == report.result_fingerprint
    capacity_only = await simulate(service, world, aid, workload=WORKLOAD, analyses=(A.CAPACITY,))
    assert capacity_only.simulation.status == "completed"


async def test_the_architecture_is_never_changed(
    service: SimulationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    before = await ArchitectureService(uow, clock=clock).get(
        project_id=world.project.id, architecture_id=aid, user_id=world.ada.id
    )
    scale = Scenario("Scale", changes=(ConfigurationChange("api", "replicas", 4),))
    report = await simulate(service, world, aid, scenario=scale, workload=WORKLOAD)
    assert report.overlay["changes"]
    after = await ArchitectureService(uow, clock=clock).get(
        project_id=world.project.id, architecture_id=aid, user_id=world.ada.id
    )
    assert after == before


async def test_a_failure_scenario_reports_entry_impacts(
    service: SimulationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await simulate(service, world, aid, scenario=OUTAGE)
    [run] = report.runs
    assert run.analysis is A.RELIABILITY
    assert [(e.entry_id, e.impact) for e in report.entries] == [("web", Impact.INTERRUPTED)]
    unavailable = await service.list_components(
        project_id=world.project.id,
        architecture_id=aid,
        simulation_id=report.simulation.id,
        user_id=world.ada.id,
        query=SimulationComponentQuery(unavailable=True),
    )
    assert [c.node_id for c in unavailable.items] == ["db"]


async def test_the_same_inputs_give_the_same_result(
    service: SimulationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    first = await simulate(service, world, aid, workload=WORKLOAD)
    second = await simulate(service, world, aid, workload=WORKLOAD)
    assert first.simulation.id != second.simulation.id
    assert first.result_fingerprint == second.result_fingerprint
    assert uow.simulations.deltas[first.simulation.id] == uow.simulations.deltas[second.simulation.id]


async def test_invalid_requests_store_nothing(
    service: SimulationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    recorded = len(uow.audit.events)
    ghost = Scenario("Ghost", failures=(Failure(FailureKind.COMPONENT, "ghost"),))
    unknown = PricingInputs(uuid.uuid4(), date(2026, 9, 27))
    for kwargs, error in (
        ({"scenario": ghost}, InvalidSimulationRequest),
        ({"revision_number": 9}, ArchitectureRevisionNotFound),
        ({"pricing": unknown}, PricingSnapshotNotFound),
    ):
        with pytest.raises(error):
            await simulate(service, world, aid, **kwargs)
    assert uow.simulations.reports == {}
    assert len(uow.audit.events) == recorded


class Broken:
    def simulate(self, *args: Any) -> Any:
        raise RuntimeError("engine bug: password=hunter2")

    def catalog(self) -> dict[str, Any]:
        return {}


async def test_an_engine_failure_is_a_failed_simulation_without_internals(
    uow: FakeUnitOfWork, clock: FakeClock, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    aid = await architecture(uow, clock, world)
    with caplog.at_level(logging.ERROR, logger="architectos.simulation"):
        report = await simulate(SimulationService(uow, Broken(), clock=clock), world, aid)
    assert report.simulation.status == "failed"
    assert report.simulation.error is not None
    assert report.simulation.error.code == "engine_error"
    assert "hunter2" not in report.simulation.error.message
    assert "hunter2" not in caplog.text  # logged by the error's type only
    assert [r.error_type for r in caplog.records] == ["RuntimeError"]  # type: ignore[attr-defined]
    assert (report.summary, report.overlay, uow.simulations.deltas[report.simulation.id]) == (None, None, ())


async def test_a_storage_failure_is_raised_not_swallowed(
    service: SimulationService,
    uow: FakeUnitOfWork,
    clock: FakeClock,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aid = await architecture(uow, clock, world)

    async def failing(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("database went away")

    monkeypatch.setattr(uow.simulations, "add", failing)
    recorded = len(uow.audit.events)
    with pytest.raises(ConnectionError):
        await simulate(service, world, aid)
    assert len(uow.audit.events) == recorded


async def test_access(
    service: SimulationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await simulate(service, world, aid)
    with pytest.raises(PermissionDenied):
        await simulate(service, world, aid, user_id=world.vic.id)
    common = {"project_id": world.project.id, "architecture_id": aid, "simulation_id": report.simulation.id}
    assert (await service.get(**common, user_id=world.vic.id)).simulation.id == report.simulation.id
    with pytest.raises(ProjectNotFound):
        await service.get(**common, user_id=world.eve.id)
    other, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Other"
    )
    with pytest.raises(SimulationNotFound):
        await service.get(**(common | {"architecture_id": other.id}), user_id=world.ada.id)
    with pytest.raises(ArchitectureNotFound):
        await service.get(**(common | {"project_id": world.other.id}), user_id=world.ada.id)


async def test_archived_architectures_and_projects_are_not_simulated(
    service: SimulationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    architectures = ArchitectureService(uow, clock=clock)
    await architectures.archive(project_id=world.project.id, architecture_id=aid, user_id=world.ada.id)
    with pytest.raises(ArchitectureArchived):
        await simulate(service, world, aid)
    await architectures.restore(project_id=world.project.id, architecture_id=aid, user_id=world.ada.id)
    await ProjectService(uow, clock=clock).archive(project_id=world.project.id, user_id=world.ada.id)
    with pytest.raises(ProjectArchived):
        await simulate(service, world, aid)


async def test_reading_lists_simulations_components_and_deltas(
    service: SimulationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    ids = []
    for _ in range(3):
        ids.append((await simulate(service, world, aid, workload=WORKLOAD)).simulation.id)
        clock.advance(timedelta(seconds=1))
    page = await service.list_simulations(
        project_id=world.project.id, architecture_id=aid, user_id=world.ada.id, limit=2
    )
    rest = await service.list_simulations(
        project_id=world.project.id,
        architecture_id=aid,
        user_id=world.ada.id,
        cursor=page.next_cursor,
        limit=2,
    )
    assert [r.simulation.id for r in (*page.items, *rest.items)] == ids[::-1]
    common = {"project_id": world.project.id, "architecture_id": aid, "simulation_id": ids[0]}
    first = await service.list_deltas(**common, user_id=world.ada.id, query=SimulationDeltaQuery(limit=1))
    others = await service.list_deltas(
        **common, user_id=world.ada.id, query=SimulationDeltaQuery(limit=500), cursor=first.next_cursor
    )
    assert [*first.items, *others.items] == list(uow.simulations.deltas[ids[0]])  # canonical order, paged
    api = await service.list_deltas(
        **common, user_id=world.vic.id, query=SimulationDeltaQuery(element_id="api")
    )
    assert api.items
    assert {d.element_id for d in api.items} == {"api"}
    changed = await service.list_components(**common, user_id=world.ada.id, query=SimulationComponentQuery())
    assert [c.node_id for c in changed.items] == sorted(c.node_id for c in changed.items)


async def test_simulations_are_compared_only_on_a_common_baseline(
    service: SimulationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    double = await simulate(service, world, aid, workload=WORKLOAD)
    triple = Scenario("Triple", workload=WorkloadChange(growth=Decimal(3)))
    tripled = await simulate(service, world, aid, scenario=triple, workload=WORKLOAD)
    common = {"project_id": world.project.id, "architecture_id": aid, "user_id": world.ada.id}
    comparison = await service.compare(
        **common, first_id=double.simulation.id, second_id=tripled.simulation.id
    )
    capacity = {a.analysis: a for a in comparison.analyses}[A.CAPACITY]
    assert capacity.comparable
    demand = next(d for d in comparison.deltas if (d.element_id, d.metric) == ("api", "work_rate.demand"))
    assert (demand.baseline, demand.scenario) == (Decimal(200), Decimal(300))
    failed = await simulate(SimulationService(uow, Broken(), clock=clock), world, aid)
    with pytest.raises(InvalidSimulationRequest):
        await service.compare(**common, first_id=double.simulation.id, second_id=failed.simulation.id)


def test_the_catalog(uow: FakeUnitOfWork) -> None:
    catalog = SimulationService(uow, DeterministicSimulationEngine()).catalog()
    assert len(catalog["scenario_types"]) == 9
    assert [e["analysis"] for e in catalog["evaluators"]] == ["capacity", "reliability", "cost"]
    assert catalog["limits"]["max_deltas"] == 20000


async def test_the_engine_runs_off_the_event_loop(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    real, loop_thread, seen = DeterministicSimulationEngine(), threading.get_ident(), []

    class Recording:
        def simulate(self, *args: Any) -> Any:
            seen.append(threading.get_ident())
            return real.simulate(*args)

        def catalog(self) -> Any:
            return real.catalog()

    await simulate(SimulationService(uow, Recording(), clock=clock), world, aid)
    assert seen
    assert seen[0] != loop_thread


async def test_an_archive_during_the_calculation_refuses_the_store(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    real = DeterministicSimulationEngine()

    class ArchivingMeanwhile:
        def simulate(self, *args: Any) -> Any:
            project = uow.projects.by_id[world.project.id]
            uow.projects.by_id[world.project.id] = project.archive(clock.now)
            return real.simulate(*args)

        def catalog(self) -> Any:
            return real.catalog()

    recorded = len(uow.audit.events)
    with pytest.raises(ProjectArchived):
        await simulate(SimulationService(uow, ArchivingMeanwhile(), clock=clock), world, aid)
    assert uow.simulations.reports == {}
    assert len(uow.audit.events) == recorded
