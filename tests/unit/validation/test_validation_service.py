"""The validation use cases (Milestone 6, phase 6): running, storing, reading and access."""

import dataclasses
import uuid
from datetime import timedelta
from typing import Any

import pytest

from core.architecture_ir.configuration import Configuration
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.traceability import RequirementRef
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.errors import (
    ArchitectureArchived,
    ArchitectureNotFound,
    ArchitectureRevisionNotFound,
)
from core.domain.audit.entities import AuditAction
from core.domain.organizations.errors import PermissionDenied
from core.domain.pagination import InvalidCursor
from core.domain.projects.errors import ProjectArchived, ProjectNotFound
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.projects.project_service import ProjectService
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.requirements.requirement_service import RequirementService
from core.domain.validation.errors import InvalidValidationConfig, ValidationRunNotFound
from core.domain.validation.options import ValidationConfig
from core.domain.validation.queries import FindingQuery
from core.domain.validation.results import Severity, ValidationResult, Verdict
from core.domain.validation.runs import RunStatus
from core.domain.validation.validation_service import ValidationService
from engines.validation.service import DeterministicValidationEngine
from tests.unit.architecture.world import World, make_world
from tests.unit.architecture_ir.builders import api_and_postgres
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def service(uow: FakeUnitOfWork, clock: FakeClock) -> ValidationService:
    return ValidationService(uow, DeterministicValidationEngine(), clock=clock)


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    return await make_world(uow, clock)


def insecure() -> ArchitectureIR:
    """The API reads the database with tls off: a policy violation when TLS is required."""
    base = api_and_postgres()
    connections = tuple(
        dataclasses.replace(c, configuration=Configuration({"tls": False})) if c.id == "api-db" else c
        for c in base.connections
    )
    return api_and_postgres(connections=connections)


async def architecture(
    uow: FakeUnitOfWork, clock: FakeClock, world: World, ir: ArchitectureIR | None = None
) -> uuid.UUID:
    created, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Orders", ir=ir or insecure()
    )
    return created.id


async def validate(
    service: ValidationService, world: World, architecture_id: uuid.UUID, **kwargs: Any
) -> Any:
    return await service.validate(
        project_id=world.project.id,
        architecture_id=architecture_id,
        user_id=kwargs.pop("user_id", world.ada.id),
        config=kwargs.pop("config", ValidationConfig()),
        **kwargs,
    )


# --- running ---------------------------------------------------------------------------------------


async def test_a_run_is_stored_completed_with_its_inputs_and_audited(
    service: ValidationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    await ProjectService(uow, clock=clock).update_policy(
        project_id=world.project.id, user_id=world.ada.id, policy=ArchitecturePolicy(require_tls=True)
    )
    aid = await architecture(uow, clock, world)

    report = await validate(service, world, aid)

    run = report.run
    assert (run.status, run.revision_number, run.profile) == (RunStatus.COMPLETED, 1, "default")
    assert run.started_at is not None
    assert run.completed_at is not None
    assert report.summary is not None
    assert report.summary.blocking == 1  # tls false on api-db
    assert report.inputs.policy == ArchitecturePolicy(require_tls=True).to_dict()
    assert report.inputs.requirements == ()
    assert [x.code for x in report.limitations] == ["catalog_unavailable"]
    event = uow.audit.events[-1]
    assert (event.action, event.resource_id) == (AuditAction.ARCHITECTURE_VALIDATED, aid)
    assert event.metadata["run_id"] == str(run.id)
    assert (event.metadata["status"], event.metadata["blocking"]) == ("completed", 1)
    assert "api-db" not in repr(event.metadata)  # identifiers and counts, never findings


async def test_the_same_revision_gives_the_same_result(
    service: ValidationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    first, second = await validate(service, world, aid), await validate(service, world, aid)
    assert first.run.id != second.run.id
    assert first.result_fingerprint == second.result_fingerprint
    assert first.context_fingerprint == second.context_fingerprint


async def test_requirements_are_given_to_the_engine_and_recorded(
    service: ValidationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    requirement = await RequirementService(uow, clock=clock).create(
        project_id=world.project.id,
        user_id=world.ada.id,
        type=RequirementType.SECURITY,
        category="encryption",
        title="TLS everywhere",
        statement="All traffic is encrypted in transit.",
        priority=RequirementPriority.CRITICAL,
        status=RequirementStatus.ACTIVE,
    )
    ir = dataclasses.replace(insecure(), requirement_refs=(RequirementRef(requirement.id),))
    aid = await architecture(uow, clock, world, ir)

    report = await validate(service, world, aid)

    assert report.inputs.requirements == ((str(requirement.id), 1, "active"),)
    [verdict] = report.requirement_results
    assert (verdict.reference, verdict.verdict) == (requirement.reference, Verdict.VIOLATED)


async def test_an_older_revision_can_be_validated(
    service: ValidationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    await ArchitectureService(uow, clock=clock).replace(
        project_id=world.project.id,
        architecture_id=aid,
        user_id=world.ada.id,
        base_version=1,
        ir=api_and_postgres(),
    )
    older = await validate(service, world, aid, revision_number=1)
    current = await validate(service, world, aid)
    assert (older.run.revision_number, current.run.revision_number) == (1, 2)
    with pytest.raises(ArchitectureRevisionNotFound):
        await validate(service, world, aid, revision_number=9)


async def test_an_invalid_configuration_stores_nothing(
    service: ValidationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    recorded = len(uow.audit.events)
    with pytest.raises(InvalidValidationConfig):
        await validate(service, world, aid, config=ValidationConfig(profile="paranoid"))
    assert uow.validations.reports == {}
    assert len(uow.audit.events) == recorded


class Broken:
    def validate(self, *args: Any, **kwargs: Any) -> ValidationResult:
        raise RuntimeError("engine bug with internal detail")

    def rules(self) -> tuple[dict[str, Any], ...]:
        return ()


async def test_an_engine_failure_is_a_failed_run_without_internals(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    broken = ValidationService(uow, Broken(), clock=clock)
    report = await validate(broken, world, aid)
    assert report.run.status is RunStatus.FAILED
    assert report.run.error is not None
    assert report.run.error.code == "engine_error"
    assert "internal" not in report.run.error.message
    assert report.summary is None
    assert uow.audit.events[-1].metadata["error"] == "engine_error"


# --- access ----------------------------------------------------------------------------------------


async def test_viewers_read_but_cannot_validate_and_strangers_see_nothing(
    service: ValidationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await validate(service, world, aid)
    with pytest.raises(PermissionDenied):
        await validate(service, world, aid, user_id=world.vic.id)
    seen = await service.get_run(
        project_id=world.project.id, architecture_id=aid, run_id=report.run.id, user_id=world.vic.id
    )
    assert seen.run.id == report.run.id
    with pytest.raises(ProjectNotFound):
        await validate(service, world, aid, user_id=world.eve.id)
    with pytest.raises(ProjectNotFound):
        await service.get_run(
            project_id=world.project.id, architecture_id=aid, run_id=report.run.id, user_id=world.eve.id
        )


async def test_runs_are_only_found_through_their_own_architecture(
    service: ValidationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await validate(service, world, aid)
    other, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Other"
    )
    with pytest.raises(ValidationRunNotFound):
        await service.get_run(
            project_id=world.project.id, architecture_id=other.id, run_id=report.run.id, user_id=world.ada.id
        )
    with pytest.raises(ArchitectureNotFound):  # an architecture of another project
        await service.get_run(
            project_id=world.other.id, architecture_id=aid, run_id=report.run.id, user_id=world.ada.id
        )


async def test_archived_architectures_and_projects_cannot_be_validated(
    service: ValidationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    architectures = ArchitectureService(uow, clock=clock)
    await architectures.archive(project_id=world.project.id, architecture_id=aid, user_id=world.ada.id)
    with pytest.raises(ArchitectureArchived):
        await validate(service, world, aid)
    await architectures.restore(project_id=world.project.id, architecture_id=aid, user_id=world.ada.id)
    await ProjectService(uow, clock=clock).archive(project_id=world.project.id, user_id=world.ada.id)
    with pytest.raises(ProjectArchived):
        await validate(service, world, aid)


# --- reading ---------------------------------------------------------------------------------------


async def test_runs_are_listed_newest_first_and_paged(
    service: ValidationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    ids = []
    for _ in range(3):
        ids.append((await validate(service, world, aid)).run.id)
        clock.advance(timedelta(seconds=1))
    first = await service.list_runs(
        project_id=world.project.id, architecture_id=aid, user_id=world.ada.id, limit=2
    )
    assert [r.run.id for r in first.items] == ids[:0:-1]
    rest = await service.list_runs(
        project_id=world.project.id,
        architecture_id=aid,
        user_id=world.ada.id,
        cursor=first.next_cursor,
        limit=2,
    )
    assert ([r.run.id for r in rest.items], rest.next_cursor) == ([ids[0]], None)
    with pytest.raises(InvalidCursor):
        await service.list_runs(
            project_id=world.project.id, architecture_id=aid, user_id=world.ada.id, cursor="nope"
        )


async def test_findings_are_paged_in_order_and_filtered(
    service: ValidationService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    await ProjectService(uow, clock=clock).update_policy(
        project_id=world.project.id,
        user_id=world.ada.id,
        policy=ArchitecturePolicy(
            require_tls=True, prohibited_technologies=frozenset({"postgresql", "fastapi"})
        ),
    )
    aid = await architecture(uow, clock, world)
    report = await validate(service, world, aid)
    assert report.summary is not None
    total = report.summary.total
    assert total >= 3

    async def page(cursor: str | None = None, **filters: Any) -> Any:
        return await service.list_findings(
            project_id=world.project.id,
            architecture_id=aid,
            run_id=report.run.id,
            user_id=world.ada.id,
            query=FindingQuery(limit=filters.pop("limit", 100), **filters),
            cursor=cursor,
        )

    everything = (await page()).items
    first = await page(limit=2)
    second = await page(first.next_cursor, limit=100)
    assert [*first.items, *second.items] == everything
    assert [f.severity.rank for f in everything] == sorted(f.severity.rank for f in everything)
    assert {f.code for f in (await page(blocking=True)).items} == {"prohibited_technology", "tls_disabled"}
    assert all(f.severity is Severity.HIGH for f in (await page(severity=Severity.HIGH)).items)
    assert [f.entity_ids for f in (await page(entity_id="api-db")).items] == [("api-db",)]
    assert {f.rule_id for f in (await page(rule_id="policy.tls")).items} == {"policy.tls"}


def test_the_rule_catalog_describes_every_rule(uow: FakeUnitOfWork) -> None:
    rules = ValidationService(uow, DeterministicValidationEngine()).rules()
    ids = [r["id"] for r in rules]
    assert ids == sorted(ids)
    assert {"structure.schema-version", "policy.tls", "requirements.verdicts"} <= set(ids)
    assert all(
        {"id", "version", "category", "severity", "profiles", "mandatory", "parameters"} <= set(r)
        for r in rules
    )
