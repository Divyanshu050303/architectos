"""Reliability use cases (Milestone 9, phase 8): running, storing, reading and access."""

import threading
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
from core.domain.capacity.results import ComponentStatus
from core.domain.capacity.units import Quantity
from core.domain.organizations.errors import PermissionDenied
from core.domain.projects.errors import ProjectArchived, ProjectNotFound
from core.domain.projects.project_service import ProjectService
from core.domain.reliability.analyses import Objective
from core.domain.reliability.errors import InvalidReliabilityRequest, ReliabilityAnalysisNotFound
from core.domain.reliability.queries import ReliabilityComponentQuery, ReliabilityFindingQuery
from core.domain.reliability.reliability_service import ReliabilityService
from core.domain.reliability.results import FindingType, ObjectiveKind
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.requirement_service import RequirementService
from core.domain.validation.results import Severity, Verdict
from engines.reliability.service import DeterministicReliabilityEngine
from tests.unit.architecture.world import World, make_world
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork

SYNC: dict[str, Any] = {
    "kind": ConnectionKind.REQUEST,
    "protocol": "https",
    "interaction": Interaction.SYNCHRONOUS,
}


def shop() -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("api", configuration=Configuration({"availability": Decimal("0.999"), "mttr_seconds": 600})),
            node(
                "db",
                NodeKind.DATABASE,
                configuration=Configuration({"replicas": 1, "mtbf_seconds": 99, "mttr_seconds": 1}),
            ),
        ),
        connections=(
            connection("web-api", "web", "api", **SYNC),
            connection(
                "api-db",
                "api",
                "db",
                **(SYNC | {"kind": ConnectionKind.DATA_ACCESS, "protocol": "postgresql"}),
            ),
        ),
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def service(uow: FakeUnitOfWork, clock: FakeClock) -> ReliabilityService:
    return ReliabilityService(uow, DeterministicReliabilityEngine(), clock=clock)


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    return await make_world(uow, clock)


async def architecture(uow: FakeUnitOfWork, clock: FakeClock, world: World) -> uuid.UUID:
    created, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Shop", ir=shop()
    )
    return created.id


async def analyze(service: ReliabilityService, world: World, aid: uuid.UUID, **kwargs: Any) -> Any:
    return await service.analyze(
        project_id=world.project.id,
        architecture_id=aid,
        user_id=kwargs.pop("user_id", world.ada.id),
        **kwargs,
    )


async def test_an_analysis_is_stored_with_its_inputs_and_audited(
    service: ReliabilityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    slo = Objective("slo", ObjectiveKind.AVAILABILITY, target=Decimal("0.99"))
    report = await analyze(service, world, aid, label="Launch", objectives=(slo,))
    a = report.analysis
    assert (a.status, a.revision_number, a.label) == ("completed", 1, "Launch")
    [path] = report.paths
    assert path.availability.quantity == Quantity.of("0.98901", "ratio")  # 0.999 x 0.99
    [objective] = report.objectives
    assert objective.verdict is Verdict.VIOLATED
    assert report.summary["paths_estimated"] == 1
    assert report.inputs["objectives"][0]["key"] == "slo"
    stored = uow.reliability.findings[a.id]
    assert {f.type for f in stored} >= {
        FindingType.SINGLE_POINT_OF_FAILURE,
        FindingType.AVAILABILITY_BELOW_OBJECTIVE,
    }
    event = uow.audit.events[-1]
    assert (event.action, event.resource_id) == (AuditAction.ARCHITECTURE_RELIABILITY_ANALYZED, aid)
    assert event.metadata["components"] == 2
    assert "0.999" not in repr(event.metadata)


async def test_in_force_requirements_become_objectives_and_are_recorded(
    service: ReliabilityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    requirement = await RequirementService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, type=RequirementType.RELIABILITY, category="rto",
        title="Recover in 10 minutes", statement="Recovery takes at most 10 minutes.",
        priority=RequirementPriority.HIGH, status=RequirementStatus.ACTIVE,
        structured_data={"metric": "rto", "operator": "<=", "value": 10, "unit": "min"},
    )  # fmt: skip
    report = await analyze(service, world, aid)
    assert report.inputs["requirements"] == [[str(requirement.id), 1, "active"]]
    [objective] = report.objectives
    assert (objective.requirement_id, objective.verdict) == (
        str(requirement.id),
        Verdict.SATISFIED,
    )  # 600 s, 1 s


async def test_the_same_inputs_give_the_same_result(
    service: ReliabilityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    first, second = await analyze(service, world, aid), await analyze(service, world, aid)
    assert first.analysis.id != second.analysis.id
    assert first.result_fingerprint == second.result_fingerprint


async def test_invalid_requests_store_nothing(
    service: ReliabilityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    recorded = len(uow.audit.events)
    for kwargs, error in (
        ({"entries": ("ghost",)}, InvalidReliabilityRequest),
        (
            {
                "objectives": (
                    Objective("n", ObjectiveKind.REDUNDANCY, target=Decimal(2), node_ids=("nope",)),
                )
            },
            InvalidReliabilityRequest,
        ),
        ({"revision_number": 9}, ArchitectureRevisionNotFound),
    ):
        with pytest.raises(error):
            await analyze(service, world, aid, **kwargs)
    assert uow.reliability.reports == {}
    assert len(uow.audit.events) == recorded


class Broken:
    def analyze(self, *args: Any) -> Any:
        raise RuntimeError("engine bug with internal detail")

    def models(self) -> tuple[dict[str, Any], ...]:
        return ()


async def test_an_engine_failure_is_a_failed_analysis_without_internals(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await analyze(ReliabilityService(uow, Broken(), clock=clock), world, aid)
    assert report.analysis.status == "failed"
    assert report.analysis.error is not None
    assert (report.analysis.error.code, "internal" in report.analysis.error.message) == (
        "engine_error",
        False,
    )
    assert (report.summary, uow.reliability.components[report.analysis.id]) == (None, ())


async def test_a_storage_failure_is_raised_not_swallowed(
    service: ReliabilityService,
    uow: FakeUnitOfWork,
    clock: FakeClock,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aid = await architecture(uow, clock, world)

    async def failing(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("database went away")

    monkeypatch.setattr(uow.reliability, "add", failing)
    recorded = len(uow.audit.events)
    with pytest.raises(ConnectionError):
        await analyze(service, world, aid)
    assert len(uow.audit.events) == recorded


async def test_access(
    service: ReliabilityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await analyze(service, world, aid)
    with pytest.raises(PermissionDenied):
        await analyze(service, world, aid, user_id=world.vic.id)
    common = {"project_id": world.project.id, "architecture_id": aid, "analysis_id": report.analysis.id}
    assert (await service.get(**common, user_id=world.vic.id)).analysis.id == report.analysis.id
    with pytest.raises(ProjectNotFound):
        await service.get(**common, user_id=world.eve.id)
    other, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Other"
    )
    with pytest.raises(ReliabilityAnalysisNotFound):
        await service.get(**(common | {"architecture_id": other.id}), user_id=world.ada.id)
    with pytest.raises(ArchitectureNotFound):
        await service.get(**(common | {"project_id": world.other.id}), user_id=world.ada.id)


async def test_archived_architectures_and_projects_are_not_analyzed(
    service: ReliabilityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
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


async def test_reading_lists_analyses_components_and_findings(
    service: ReliabilityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
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
    first = await service.list_components(**common, query=ReliabilityComponentQuery(limit=1))
    second = await service.list_components(
        **common, query=ReliabilityComponentQuery(limit=5), cursor=first.next_cursor
    )
    assert [c.node_id for c in (*first.items, *second.items)] == ["api", "db"]
    estimated = await service.list_components(
        **common, query=ReliabilityComponentQuery(ComponentStatus.ESTIMATED)
    )
    assert [c.node_id for c in estimated.items] == ["api", "db"]
    top = await service.list_findings(**common, query=ReliabilityFindingQuery(limit=1))
    others = await service.list_findings(
        **common, query=ReliabilityFindingQuery(limit=100), cursor=top.next_cursor
    )
    everything = [*top.items, *others.items]
    assert everything == list(uow.reliability.findings[ids[0]])  # canonical order, paged
    high = await service.list_findings(**common, query=ReliabilityFindingQuery(severity=Severity.HIGH))
    assert {f.type for f in high.items} == {FindingType.SINGLE_POINT_OF_FAILURE}


def test_the_model_catalog(uow: FakeUnitOfWork) -> None:
    models = ReliabilityService(uow, DeterministicReliabilityEngine()).models()
    assert [m["id"] for m in models if m["type"] == "model"][:3] == [
        "declared-availability", "declared-replica-availability", "mtbf-mttr",
    ]  # fmt: skip
    assert [m["id"] for m in models if m["type"] == "step"] == [
        "request-paths", "path-findings", "topology-findings", "objectives",
    ]  # fmt: skip


async def test_the_engine_runs_off_the_event_loop(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    real, loop_thread, seen = DeterministicReliabilityEngine(), threading.get_ident(), []

    class Recording:
        def analyze(self, *args: Any) -> Any:
            seen.append(threading.get_ident())
            return real.analyze(*args)

        def models(self) -> Any:
            return real.models()

    await analyze(ReliabilityService(uow, Recording(), clock=clock), world, aid)
    assert seen
    assert seen[0] != loop_thread


async def test_an_archive_during_the_calculation_refuses_the_store(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    real = DeterministicReliabilityEngine()

    class ArchivingMeanwhile:
        def analyze(self, *args: Any) -> Any:
            project = uow.projects.by_id[world.project.id]
            uow.projects.by_id[world.project.id] = project.archive(clock.now)
            return real.analyze(*args)

        def models(self) -> Any:
            return real.models()

    recorded = len(uow.audit.events)
    with pytest.raises(ProjectArchived):
        await analyze(ReliabilityService(uow, ArchivingMeanwhile(), clock=clock), world, aid)
    assert uow.reliability.reports == {}
    assert len(uow.audit.events) == recorded
