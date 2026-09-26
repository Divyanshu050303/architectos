"""Cost use cases (Milestone 8, phase 8): running (with a capacity analysis and scenarios), storing,
reading, and access."""

import dataclasses
import uuid
from datetime import UTC, date, datetime, timedelta
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
from core.domain.capacity.capacity_service import CapacityService
from core.domain.capacity.errors import CapacityAnalysisNotFound
from core.domain.capacity.scenarios import ConfigurationChange, Scenario
from core.domain.cost.analyses import CostAssumption
from core.domain.cost.cost_service import CostService
from core.domain.cost.errors import (
    CostAnalysisNotFound,
    IncompatibleCapacityAnalysis,
    InvalidCostRequest,
    PricingSnapshotNotFound,
)
from core.domain.cost.pricing import PricingSnapshot
from core.domain.cost.projection import CostScenario
from core.domain.cost.queries import LineItemQuery
from core.domain.cost.results import CostCategory, LineStatus
from core.domain.organizations.errors import PermissionDenied
from core.domain.projects.errors import ProjectArchived, ProjectNotFound
from core.domain.projects.project_service import ProjectService
from core.domain.projects.value_objects import CloudProvider, ProjectSettings
from engines.capacity.service import DeterministicCapacityEngine
from engines.cost.service import DeterministicCostEngine
from tests.unit.architecture.world import World, make_world
from tests.unit.cost.test_cost_capacity import IR, PRICES, workload
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 26, 12, tzinfo=UTC))


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def service(uow: FakeUnitOfWork, clock: FakeClock) -> CostService:
    return CostService(uow, DeterministicCostEngine(), clock=clock)


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    world = await make_world(uow, clock)
    await ProjectService(uow, clock=clock).update(
        project_id=world.project.id, user_id=world.ada.id, settings=ProjectSettings(CloudProvider.AWS, "USD")
    )
    return world


async def setup(uow: FakeUnitOfWork, clock: FakeClock, world: World) -> tuple[uuid.UUID, uuid.UUID]:
    created, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Shop", ir=IR
    )
    snapshot = PricingSnapshot(uuid.uuid7(), world.project.organization_id, "Prices", PRICES, clock.now)
    await uow.pricing.add(snapshot)
    return created.id, snapshot.id


async def capacity(uow: FakeUnitOfWork, clock: FakeClock, world: World, aid: uuid.UUID) -> uuid.UUID:
    report = await CapacityService(uow, DeterministicCapacityEngine(), clock=clock).analyze(
        project_id=world.project.id, architecture_id=aid, user_id=world.ada.id, workload=workload()
    )
    return report.analysis.id


async def analyze(
    service: CostService, world: World, aid: uuid.UUID, snapshot: uuid.UUID, **kwargs: Any
) -> Any:
    return await service.analyze(
        project_id=world.project.id,
        architecture_id=aid,
        user_id=kwargs.pop("user_id", world.ada.id),
        snapshot_id=snapshot,
        **kwargs,
    )


async def test_an_analysis_is_stored_with_its_inputs_and_audited(
    service: CostService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid, snapshot = await setup(uow, clock, world)
    report = await analyze(
        service,
        world,
        aid,
        snapshot,
        label="Launch",
        assumptions=(CostAssumption("on_demand", "No discounts."),),
    )
    a = report.analysis
    assert (a.status, a.revision_number, a.label, report.currency) == ("partial", 1, "Launch", "USD")
    assert report.inputs["pricing_date"] == "2026-09-26"  # today, by default
    assert report.inputs["provider"] == "aws"
    assert report.totals is not None
    assert report.totals.monthly.amount == Decimal(
        "82.568"
    )  # api 59.568 + files 23 (declared); usage unknown
    assert report.summary is not None
    assert report.summary["known_total_is_lower_bound"] is True
    assert {e.label for e in report.assumptions} >= {
        "hours_per_month",
        "operating_hours_per_month",
        "currency",
    }
    event = uow.audit.events[-1]
    assert (event.action, event.resource_id) == (AuditAction.ARCHITECTURE_COST_ANALYZED, aid)
    assert event.metadata["analysis_id"] == str(a.id)
    assert event.metadata["line_items"] == 5
    assert "59.568" not in repr(event.metadata)  # identifiers and counts only, never amounts


async def test_capacity_usage_and_scenarios(
    service: CostService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid, snapshot = await setup(uow, clock, world)
    cid = await capacity(uow, clock, world, aid)
    report = await analyze(
        service, world, aid, snapshot, capacity_analysis_id=cid, replicas_from_capacity=True,
        scenarios=(
            CostScenario(Scenario("Double", growth=Decimal(2))),
            CostScenario(Scenario("Bigger", changes=(ConfigurationChange("api", "replicas", Decimal(3)),))),
        ),
    )  # fmt: skip
    assert report.analysis.status == "completed"
    assert report.totals.monthly.amount == Decimal("253.8229872")  # 119.136 stepwise + 134.6869872 usage
    double, bigger = report.scenarios
    assert double["name"] == "Double"
    assert double["comparison"]["complete"] is True
    assert double["comparison"]["difference"]["month"]["amount"] == "253.8229872"  # everything doubles
    # 3 declared replicas need no scaling, so the declared 3 are billed (instead of the required 2)
    [api] = bigger["comparison"]["changed_lines"]
    assert (api["element_id"], api["baseline_quantity"], api["scenario_quantity"]) == ("api", "1460", "2190")


async def test_invalid_requests_store_nothing(
    service: CostService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid, snapshot = await setup(uow, clock, world)
    cid = await capacity(uow, clock, world, aid)
    recorded = len(uow.audit.events)
    foreign = PricingSnapshot(uuid.uuid7(), uuid.uuid7(), "Theirs", PRICES, clock.now)
    await uow.pricing.add(foreign)
    cases: tuple[tuple[dict[str, Any], type[Exception]], ...] = (
        ({"snapshot": foreign.id}, PricingSnapshotNotFound),
        ({"capacity_analysis_id": uuid.uuid7()}, CapacityAnalysisNotFound),
        ({"operating_hours_per_month": Decimal(0)}, InvalidCostRequest),
        ({"currency": "usd"}, InvalidCostRequest),
        ({"replicas_from_capacity": True}, InvalidCostRequest),
        ({"scenarios": (CostScenario(Scenario("x")), CostScenario(Scenario("x")))}, InvalidCostRequest),
        ({"scenarios": (CostScenario(Scenario("Double", growth=Decimal(2))),)}, InvalidCostRequest),
        ({"revision_number": 9}, ArchitectureRevisionNotFound),
    )
    for kwargs, error in cases:
        with pytest.raises(error):
            await analyze(service, world, aid, kwargs.pop("snapshot", snapshot), **kwargs)
    # A capacity analysis of revision 1 cannot price revision 2.
    await ArchitectureService(uow, clock=clock).replace(
        project_id=world.project.id, architecture_id=aid, user_id=world.ada.id, base_version=1,
        ir=dataclasses.replace(IR, name="Shop v2"),
    )  # fmt: skip
    with pytest.raises(IncompatibleCapacityAnalysis) as mismatch:
        await analyze(service, world, aid, snapshot, capacity_analysis_id=cid)
    assert mismatch.value.details == {"reason": "revision"}
    assert uow.cost.reports == {}
    assert len(uow.audit.events) == recorded + 1  # the new revision only


class Broken:
    def analyze(self, *args: Any) -> Any:
        raise RuntimeError("engine bug with internal detail")

    def models(self) -> tuple[dict[str, Any], ...]:
        return ()


async def test_an_engine_failure_is_a_failed_analysis_without_internals(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid, snapshot = await setup(uow, clock, world)
    report = await analyze(CostService(uow, Broken(), clock=clock), world, aid, snapshot)
    assert report.analysis.status == "failed"
    assert report.analysis.error is not None
    assert (report.analysis.error.code, "internal" in report.analysis.error.message) == (
        "engine_error",
        False,
    )
    assert (report.totals, report.summary, uow.cost.line_items[report.analysis.id]) == (None, None, ())


async def test_a_storage_failure_is_raised_not_swallowed(
    service: CostService, uow: FakeUnitOfWork, clock: FakeClock, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    aid, snapshot = await setup(uow, clock, world)

    async def failing(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("database went away")

    monkeypatch.setattr(uow.cost, "add", failing)
    recorded = len(uow.audit.events)
    with pytest.raises(ConnectionError):
        await analyze(service, world, aid, snapshot)
    assert len(uow.audit.events) == recorded


async def test_access(service: CostService, uow: FakeUnitOfWork, clock: FakeClock, world: World) -> None:
    aid, snapshot = await setup(uow, clock, world)
    report = await analyze(service, world, aid, snapshot)
    with pytest.raises(PermissionDenied):
        await analyze(service, world, aid, snapshot, user_id=world.vic.id)
    common = {"project_id": world.project.id, "architecture_id": aid, "analysis_id": report.analysis.id}
    assert (await service.get(**common, user_id=world.vic.id)).analysis.id == report.analysis.id
    with pytest.raises(ProjectNotFound):
        await service.get(**common, user_id=world.eve.id)
    other, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Other"
    )
    with pytest.raises(CostAnalysisNotFound):
        await service.get(**(common | {"architecture_id": other.id}), user_id=world.ada.id)
    with pytest.raises(ArchitectureNotFound):
        await service.get(**(common | {"project_id": world.other.id}), user_id=world.ada.id)
    # a capacity analysis of another architecture is indistinguishable from a missing one
    cid = await capacity(uow, clock, world, aid)
    with pytest.raises(CapacityAnalysisNotFound):
        await analyze(service, world, other.id, snapshot, capacity_analysis_id=cid)


async def test_archived_architectures_and_projects_are_not_analyzed(
    service: CostService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid, snapshot = await setup(uow, clock, world)
    architectures = ArchitectureService(uow, clock=clock)
    await architectures.archive(project_id=world.project.id, architecture_id=aid, user_id=world.ada.id)
    with pytest.raises(ArchitectureArchived):
        await analyze(service, world, aid, snapshot)
    await architectures.restore(project_id=world.project.id, architecture_id=aid, user_id=world.ada.id)
    await ProjectService(uow, clock=clock).archive(project_id=world.project.id, user_id=world.ada.id)
    with pytest.raises(ProjectArchived):
        await analyze(service, world, aid, snapshot)


async def test_reading_lists_analyses_and_line_items(
    service: CostService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid, snapshot = await setup(uow, clock, world)
    ids = []
    for _ in range(3):
        ids.append((await analyze(service, world, aid, snapshot, pricing_date=date(2026, 9, 26))).analysis.id)
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
    first = await service.list_line_items(**common, query=LineItemQuery(limit=2))
    second = await service.list_line_items(**common, query=LineItemQuery(limit=10), cursor=first.next_cursor)
    assert [f"{x.element_id}/{x.resource}" for x in (*first.items, *second.items)] == [
        "api/instances", "bus/requests", "cdn/data_transfer", "files/storage", "logs/ingestion",
    ]  # fmt: skip
    unknown = await service.list_line_items(**common, query=LineItemQuery(status=LineStatus.UNKNOWN))
    assert [x.element_id for x in unknown.items] == ["bus", "cdn", "logs"]
    network = await service.list_line_items(**common, query=LineItemQuery(category=CostCategory.NETWORK))
    assert [x.element_id for x in network.items] == ["cdn"]
    api = await service.list_line_items(**common, query=LineItemQuery(element_id="api"))
    assert [x.resource for x in api.items] == ["instances"]


async def test_the_currency_defaults_to_the_project_and_is_never_converted(
    service: CostService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid, snapshot = await setup(uow, clock, world)
    report = await analyze(service, world, aid, snapshot, currency="EUR")
    assert report.analysis.status == "insufficient_pricing"  # every price is in USD
    assert report.totals.monthly.amount == 0


def test_the_model_catalog(uow: FakeUnitOfWork) -> None:
    models = CostService(uow, DeterministicCostEngine()).models()
    assert [m["id"] for m in models] == sorted(m["id"] for m in models)
    assert all(
        {"id", "version", "kinds", "resources", "units", "configuration", "usage"} <= set(m) for m in models
    )


async def test_an_archive_during_the_calculation_refuses_the_store(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid, snapshot = await setup(uow, clock, world)
    real = DeterministicCostEngine()

    class ArchivingMeanwhile:
        def analyze(self, *args: Any) -> Any:
            project = uow.projects.by_id[world.project.id]
            uow.projects.by_id[world.project.id] = project.archive(clock.now)
            return real.analyze(*args)

        def models(self) -> Any:
            return real.models()

    recorded = len(uow.audit.events)
    with pytest.raises(ProjectArchived):
        await analyze(CostService(uow, ArchivingMeanwhile(), clock=clock), world, aid, snapshot)
    assert uow.cost.reports == {}
    assert len(uow.audit.events) == recorded
