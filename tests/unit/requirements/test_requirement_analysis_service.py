"""Analyze, read and promote (Requirements Engine phase 10)."""

from collections.abc import Sequence

import pytest

from core.domain.identity.entities import NewUser, User
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import PermissionDenied
from core.domain.organizations.organization_service import OrganizationService
from core.domain.projects.entities import Project
from core.domain.projects.errors import ProjectArchived, ProjectNotFound
from core.domain.projects.project_service import ProjectService
from core.domain.requirements.analyses import AnalyzerOutput
from core.domain.requirements.analysis_service import MAX_PROMOTIONS, RequirementAnalysisService
from core.domain.requirements.entities import Requirement
from core.domain.requirements.enums import RequirementSource, RequirementStatus
from core.domain.requirements.errors import (
    InvalidPromotion,
    InvalidRequirementInput,
    RequirementAnalysisNotFound,
)
from engines.requirements.service import ENGINE_VERSION, RequirementsEngine
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork

TEXT = (
    "  A food delivery platform. Support at least 2,000 rps, p95 latency under 300 ms "
    "and 99.9% availability.\n"
)


class World:
    def __init__(self, ada: User, vic: User, eve: User, project: Project, other: Project) -> None:
        self.ada, self.vic, self.eve, self.project, self.other = ada, vic, eve, project, other


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def service(uow: FakeUnitOfWork, clock: FakeClock) -> RequirementAnalysisService:
    return RequirementAnalysisService(uow, RequirementsEngine(), clock=clock)


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    users = []
    for email in ("ada@example.com", "vic@example.com", "eve@example.com"):
        user = await uow.users.add(NewUser(email, email[:3], "$argon2id$x"))
        await uow.users.mark_email_verified(user.id, clock.now)
        stored = await uow.users.get(user.id)
        assert stored is not None
        users.append(stored)
    ada, vic, eve = users
    acme = await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")
    await OrganizationService(uow, clock=clock).create(user=eve, name="Globex")
    await uow.memberships.add(organization_id=acme.organization.id, user_id=vic.id, role=Role.VIEWER)
    projects = ProjectService(uow, clock=clock)
    project = await projects.create(membership=acme.membership, name="Food Delivery")
    other = await projects.create(membership=acme.membership, name="Payments")
    return World(ada, vic, eve, project, other)


# --- analyze ---------------------------------------------------------------------------------------


async def test_analysis_is_stored_and_creates_no_requirement(
    service: RequirementAnalysisService, uow: FakeUnitOfWork, world: World
) -> None:
    analysis = await service.analyze(project_id=world.project.id, user_id=world.ada.id, raw_input=TEXT)
    assert analysis.raw_input == TEXT  # exactly as written
    assert analysis.engine_version == ENGINE_VERSION
    assert analysis.result["ready_for_architecture"] is True
    assert len(analysis.result["candidates"]) == 3
    assert uow.requirements.by_id == {}  # nothing becomes canonical by analyzing
    event = uow.audit.events[-1]
    assert event.action.value == "requirement_analysis.created"
    assert event.metadata == {
        "project_id": str(world.project.id),
        "engine_version": ENGINE_VERSION,
        "candidates": 3,
        "blocking": 0,
        "ready_for_architecture": True,
        "input_characters": len(TEXT),
    }
    assert "food delivery" not in repr(event.metadata).lower()  # the text never reaches the audit log


async def test_the_engine_runs_between_transactions_and_sees_existing_requirements(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    seen: list[tuple[int, int]] = []

    class Spy:
        async def analyze(self, raw_input: str, existing: Sequence[Requirement]) -> AnalyzerOutput:
            seen.append((uow.commits, len(existing)))
            return AnalyzerOutput(ENGINE_VERSION, {"candidates": []}, False, 0, 1)

    commits_before = uow.commits
    await RequirementAnalysisService(uow, Spy(), clock=clock).analyze(
        project_id=world.project.id, user_id=world.ada.id, raw_input="Anything."
    )
    assert seen == [(commits_before + 1, 0)]  # the read transaction had ended
    assert uow.commits == commits_before + 2  # and the result was stored in its own


@pytest.mark.parametrize("raw", ["", "   ", "a\x00b", "x" * 20_001])
async def test_unusable_input_is_refused_before_anything_else(
    service: RequirementAnalysisService, uow: FakeUnitOfWork, world: World, raw: str
) -> None:
    commits = uow.commits
    with pytest.raises(InvalidRequirementInput):
        await service.analyze(project_id=world.project.id, user_id=world.ada.id, raw_input=raw)
    assert uow.commits == commits


async def test_who_may_analyze(
    service: RequirementAnalysisService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    with pytest.raises(PermissionDenied):
        await service.analyze(project_id=world.project.id, user_id=world.vic.id, raw_input=TEXT)
    with pytest.raises(ProjectNotFound):
        await service.analyze(project_id=world.project.id, user_id=world.eve.id, raw_input=TEXT)
    await ProjectService(uow, clock=clock).archive(project_id=world.project.id, user_id=world.ada.id)
    with pytest.raises(ProjectArchived):
        await service.analyze(project_id=world.project.id, user_id=world.ada.id, raw_input=TEXT)


async def test_reading_an_analysis(service: RequirementAnalysisService, world: World) -> None:
    analysis = await service.analyze(project_id=world.project.id, user_id=world.ada.id, raw_input=TEXT)
    assert (
        await service.get(project_id=world.project.id, analysis_id=analysis.id, user_id=world.vic.id)
        == analysis
    )
    with pytest.raises(RequirementAnalysisNotFound):
        await service.get(project_id=world.other.id, analysis_id=analysis.id, user_id=world.ada.id)
    with pytest.raises(ProjectNotFound):
        await service.get(project_id=world.project.id, analysis_id=analysis.id, user_id=world.eve.id)


# --- promote ---------------------------------------------------------------------------------------


async def test_promotion_creates_drafts_with_their_origin(
    service: RequirementAnalysisService, uow: FakeUnitOfWork, world: World
) -> None:
    analysis = await service.analyze(project_id=world.project.id, user_id=world.ada.id, raw_input=TEXT)
    keys = [c["key"] for c in analysis.result["candidates"]]
    promotions = await service.promote(
        project_id=world.project.id, analysis_id=analysis.id, user_id=world.ada.id, candidate_keys=keys
    )
    assert [p.created for p in promotions] == [True, True, True]
    for promotion, key in zip(promotions, keys, strict=True):
        requirement = promotion.requirement
        assert (requirement.content.status, requirement.source) == (
            RequirementStatus.DRAFT,
            RequirementSource.SYSTEM,
        )
        assert requirement.origin is not None
        assert (requirement.origin.analysis_id, requirement.origin.candidate_key) == (analysis.id, key)
        assert requirement.content.statement in TEXT
    events = [e for e in uow.audit.events if e.action.value == "requirement.promoted"]
    assert [e.metadata["candidate_key"] for e in events] == keys


async def test_promotion_is_idempotent(
    service: RequirementAnalysisService, uow: FakeUnitOfWork, world: World
) -> None:
    analysis = await service.analyze(project_id=world.project.id, user_id=world.ada.id, raw_input=TEXT)
    key = analysis.result["candidates"][0]["key"]
    [first] = await service.promote(
        project_id=world.project.id, analysis_id=analysis.id, user_id=world.ada.id, candidate_keys=[key]
    )
    [retry] = await service.promote(
        project_id=world.project.id, analysis_id=analysis.id, user_id=world.ada.id, candidate_keys=[key]
    )
    assert (first.created, retry.created) == (True, False)
    assert retry.requirement.id == first.requirement.id
    assert len(uow.requirements.by_id) == 1
    assert [e.action.value for e in uow.audit.events].count("requirement.promoted") == 1


@pytest.mark.parametrize(
    ("keys", "reason"),
    [
        ([], "empty"),
        (["cand_0000000000000000"], "unknown_candidate"),
        (["x"] * (MAX_PROMOTIONS + 1), "too_many"),
    ],
)
async def test_invalid_selections(
    service: RequirementAnalysisService, world: World, keys: list[str], reason: str
) -> None:
    analysis = await service.analyze(project_id=world.project.id, user_id=world.ada.id, raw_input=TEXT)
    with pytest.raises(InvalidPromotion) as error:
        await service.promote(
            project_id=world.project.id, analysis_id=analysis.id, user_id=world.ada.id, candidate_keys=keys
        )
    assert error.value.details["reason"] == reason


async def test_a_candidate_cannot_be_chosen_twice_in_one_request(
    service: RequirementAnalysisService, world: World
) -> None:
    analysis = await service.analyze(project_id=world.project.id, user_id=world.ada.id, raw_input=TEXT)
    key = analysis.result["candidates"][0]["key"]
    with pytest.raises(InvalidPromotion) as error:
        await service.promote(
            project_id=world.project.id,
            analysis_id=analysis.id,
            user_id=world.ada.id,
            candidate_keys=[key, key],
        )
    assert error.value.details == {"reason": "duplicate", "candidate_key": key}


async def test_who_may_promote_and_where(
    service: RequirementAnalysisService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    analysis = await service.analyze(project_id=world.project.id, user_id=world.ada.id, raw_input=TEXT)
    key = analysis.result["candidates"][0]["key"]
    with pytest.raises(PermissionDenied):
        await service.promote(
            project_id=world.project.id, analysis_id=analysis.id, user_id=world.vic.id, candidate_keys=[key]
        )
    with pytest.raises(RequirementAnalysisNotFound):
        await service.promote(
            project_id=world.other.id, analysis_id=analysis.id, user_id=world.ada.id, candidate_keys=[key]
        )
    await ProjectService(uow, clock=clock).archive(project_id=world.project.id, user_id=world.ada.id)
    with pytest.raises(ProjectArchived):
        await service.promote(
            project_id=world.project.id, analysis_id=analysis.id, user_id=world.ada.id, candidate_keys=[key]
        )
