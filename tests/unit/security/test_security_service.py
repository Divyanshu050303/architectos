"""Security use cases (Milestone 10, phase 9): running, storing, reading and access."""

import json
import logging
import threading
import uuid
from datetime import timedelta
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.errors import (
    ArchitectureArchived,
    ArchitectureNotFound,
    ArchitectureRevisionNotFound,
)
from core.domain.audit.entities import AuditAction
from core.domain.organizations.errors import PermissionDenied
from core.domain.projects.errors import ProjectArchived, ProjectNotFound
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.projects.project_service import ProjectService
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.requirement_service import RequirementService
from core.domain.security.errors import InvalidSecurityRequest, SecurityAnalysisNotFound
from core.domain.security.queries import SecurityComponentQuery, SecurityFindingQuery
from core.domain.security.results import Coverage, FindingBasis, FindingType, StrideCategory
from core.domain.security.security_service import SecurityService
from core.domain.validation.results import Verdict
from engines.security.service import DeterministicSecurityEngine
from tests.unit.architecture.world import World, make_world
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork


def shop() -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            node(
                "api",
                configuration=Configuration(
                    {"exposure": "public", "authentication": "none", "data_classification": "internal"},
                    extra={"db_password": "hunter2"},
                ),
            ),
            node(
                "db",
                NodeKind.DATABASE,
                configuration=Configuration(
                    {"exposure": "private", "personal_data": True, "encryption_at_rest": False}
                ),
            ),
        ),
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https"),
            connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql"),
        ),
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def service(uow: FakeUnitOfWork, clock: FakeClock) -> SecurityService:
    return SecurityService(uow, DeterministicSecurityEngine(), clock=clock)


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    return await make_world(uow, clock)


async def architecture(uow: FakeUnitOfWork, clock: FakeClock, world: World) -> uuid.UUID:
    created, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Shop", ir=shop()
    )
    return created.id


async def analyze(service: SecurityService, world: World, aid: uuid.UUID, **kwargs: Any) -> Any:
    return await service.analyze(
        project_id=world.project.id,
        architecture_id=aid,
        user_id=kwargs.pop("user_id", world.ada.id),
        **kwargs,
    )


async def test_an_analysis_is_stored_with_its_inputs_and_audited(
    service: SecurityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await analyze(service, world, aid, label="Launch")
    a = report.analysis
    assert (a.status, a.revision_number, a.label) == ("partial", 1, "Launch")
    stored = uow.security.findings[a.id]
    assert {f.type for f in stored} >= {
        FindingType.MISSING_AUTHENTICATION,
        FindingType.UNENCRYPTED_DATA_AT_REST,
        FindingType.SECRET_IN_CONFIGURATION,
        FindingType.THREAT_CANDIDATE,
    }
    assert report.summary["bases"]["control_gap"] >= 2
    assert report.inputs["policy"] == ArchitecturePolicy().to_dict()  # the policy as it was
    assert {c.node_id for c in uow.security.components[a.id]} == {"api", "db"}
    event = uow.audit.events[-1]
    assert (event.action, event.resource_id) == (AuditAction.ARCHITECTURE_SECURITY_ANALYZED, aid)
    assert event.metadata["components"] == 2
    everything = (
        json.dumps([f.to_dict() for f in stored]) + repr(event.metadata) + json.dumps(dict(report.inputs))
    )
    assert "hunter2" not in everything  # the secret is reported by name only


async def test_the_policy_and_requirements_are_evaluated_and_recorded(
    service: SecurityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    await ProjectService(uow, clock=clock).update_policy(
        project_id=world.project.id,
        user_id=world.ada.id,
        policy=ArchitecturePolicy(require_encryption_at_rest=True),
    )
    requirement = await RequirementService(uow, clock=clock).create(
        project_id=world.project.id,
        user_id=world.ada.id,
        type=RequirementType.SECURITY,
        category="encryption",
        title="Encrypt personal data",
        statement="Personal data is encrypted at rest.",
        priority=RequirementPriority.HIGH,
        status=RequirementStatus.ACTIVE,
    )
    report = await analyze(service, world, aid)
    assert report.inputs["policy"]["require_encryption_at_rest"] is True
    assert report.inputs["requirements"] == [[str(requirement.id), 1, "active"]]
    verdicts = {c.key: (c.verdict, c.requirement_id or c.policy_rule) for c in report.checks}
    assert verdicts == {
        "policy.require_encryption_at_rest": (Verdict.VIOLATED, "require_encryption_at_rest"),
        f"requirement.{requirement.reference.lower()}": (Verdict.VIOLATED, str(requirement.id)),
    }


async def test_the_same_inputs_give_the_same_result(
    service: SecurityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    first, second = await analyze(service, world, aid), await analyze(service, world, aid)
    assert first.analysis.id != second.analysis.id
    assert first.result_fingerprint == second.result_fingerprint
    assert [f.id for f in uow.security.findings[first.analysis.id]] == [
        f.id for f in uow.security.findings[second.analysis.id]
    ]


async def test_invalid_requests_store_nothing(
    service: SecurityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    recorded = len(uow.audit.events)
    for kwargs, error in (
        ({"scope": ("ghost",)}, InvalidSecurityRequest),
        ({"analyzers": ("scanner",)}, InvalidSecurityRequest),
        ({"revision_number": 9}, ArchitectureRevisionNotFound),
    ):
        with pytest.raises(error):
            await analyze(service, world, aid, **kwargs)
    assert uow.security.reports == {}
    assert len(uow.audit.events) == recorded


class Broken:
    def analyze(self, *args: Any) -> Any:
        raise RuntimeError("engine bug: password=hunter2")

    def analyzers(self) -> tuple[dict[str, Any], ...]:
        return ()


async def test_an_engine_failure_is_a_failed_analysis_without_internals(
    uow: FakeUnitOfWork, clock: FakeClock, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    aid = await architecture(uow, clock, world)
    with caplog.at_level(logging.ERROR, logger="architectos.security"):
        report = await analyze(SecurityService(uow, Broken(), clock=clock), world, aid)
    assert report.analysis.status == "failed"
    assert report.analysis.error is not None
    assert report.analysis.error.code == "engine_error"
    assert "hunter2" not in report.analysis.error.message
    assert "hunter2" not in caplog.text  # logged by the error's type only
    assert [r.error_type for r in caplog.records] == ["RuntimeError"]  # type: ignore[attr-defined]
    assert (report.summary, uow.security.components[report.analysis.id]) == (None, ())


async def test_a_storage_failure_is_raised_not_swallowed(
    service: SecurityService,
    uow: FakeUnitOfWork,
    clock: FakeClock,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aid = await architecture(uow, clock, world)

    async def failing(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("database went away")

    monkeypatch.setattr(uow.security, "add", failing)
    recorded = len(uow.audit.events)
    with pytest.raises(ConnectionError):
        await analyze(service, world, aid)
    assert len(uow.audit.events) == recorded


async def test_access(service: SecurityService, uow: FakeUnitOfWork, clock: FakeClock, world: World) -> None:
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
    with pytest.raises(SecurityAnalysisNotFound):
        await service.get(**(common | {"architecture_id": other.id}), user_id=world.ada.id)
    with pytest.raises(ArchitectureNotFound):
        await service.get(**(common | {"project_id": world.other.id}), user_id=world.ada.id)


async def test_archived_architectures_and_projects_are_not_analyzed(
    service: SecurityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
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
    service: SecurityService, uow: FakeUnitOfWork, clock: FakeClock, world: World
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
    first = await service.list_components(**common, query=SecurityComponentQuery(limit=1))
    second = await service.list_components(
        **common, query=SecurityComponentQuery(limit=5), cursor=first.next_cursor
    )
    assert [c.node_id for c in (*first.items, *second.items)] == ["api", "db"]
    partial = await service.list_components(**common, query=SecurityComponentQuery(Coverage.PARTIAL))
    assert [c.node_id for c in partial.items] == ["api", "db"]
    top = await service.list_findings(**common, query=SecurityFindingQuery(limit=1))
    others = await service.list_findings(
        **common, query=SecurityFindingQuery(limit=500), cursor=top.next_cursor
    )
    assert [*top.items, *others.items] == list(uow.security.findings[ids[0]])  # canonical order, paged
    gaps = await service.list_findings(**common, query=SecurityFindingQuery(basis=FindingBasis.CONTROL_GAP))
    assert gaps.items
    assert {f.basis for f in gaps.items} == {FindingBasis.CONTROL_GAP}
    spoofing = await service.list_findings(
        **common, query=SecurityFindingQuery(threat=StrideCategory.SPOOFING)
    )
    assert {f.threat for f in spoofing.items} == {StrideCategory.SPOOFING}


def test_the_analyzer_catalog(uow: FakeUnitOfWork) -> None:
    analyzers = SecurityService(uow, DeterministicSecurityEngine()).analyzers()
    assert [a["id"] for a in analyzers] == [
        "trust-boundaries", "authentication", "authorization", "encryption", "data-protection",
        "secrets", "exposure", "threat-model", "policy", "requirements",
    ]  # fmt: skip
    assert all(a["rules"] and a["finding_types"] for a in analyzers)


async def test_the_engine_runs_off_the_event_loop(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    real, loop_thread, seen = DeterministicSecurityEngine(), threading.get_ident(), []

    class Recording:
        def analyze(self, *args: Any) -> Any:
            seen.append(threading.get_ident())
            return real.analyze(*args)

        def analyzers(self) -> Any:
            return real.analyzers()

    await analyze(SecurityService(uow, Recording(), clock=clock), world, aid)
    assert seen
    assert seen[0] != loop_thread


async def test_an_archive_during_the_calculation_refuses_the_store(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    real = DeterministicSecurityEngine()

    class ArchivingMeanwhile:
        def analyze(self, *args: Any) -> Any:
            project = uow.projects.by_id[world.project.id]
            uow.projects.by_id[world.project.id] = project.archive(clock.now)
            return real.analyze(*args)

        def analyzers(self) -> Any:
            return real.analyzers()

    recorded = len(uow.audit.events)
    with pytest.raises(ProjectArchived):
        await analyze(SecurityService(uow, ArchivingMeanwhile(), clock=clock), world, aid)
    assert uow.security.reports == {}
    assert len(uow.audit.events) == recorded
