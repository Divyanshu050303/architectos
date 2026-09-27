"""Evolution and decision use cases (Milestone 13, phase 9): running, storing and reading analyses,
their candidates and diffs; drafting and deciding ADRs; access, archived architectures, audit; the
architecture never changed."""

import json
import logging
import uuid
from datetime import timedelta
from typing import Any

import pytest

from core.architecture_ir.serialization import to_dict
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.errors import ArchitectureArchived, ArchitectureNotFound
from core.domain.audit.entities import AuditAction
from core.domain.capacity.capacity_service import CapacityService
from core.domain.capacity.units import Quantity
from core.domain.decisions.decision_service import DecisionService
from core.domain.decisions.entities import DecisionStatus
from core.domain.decisions.errors import DecisionNotFound, InvalidDecision, InvalidDecisionTransition
from core.domain.evolution.entities import EvidenceCitation
from core.domain.evolution.errors import (
    CandidateNotFound,
    EvolutionAnalysisNotFound,
    InvalidEvolutionRequest,
)
from core.domain.evolution.evolution_service import EvolutionService
from core.domain.evolution.goals import EvolutionGoal
from core.domain.evolution.queries import CandidateQuery
from core.domain.evolution.values import CandidateCategory, EvidenceSource, GoalType
from core.domain.organizations.errors import PermissionDenied
from core.domain.projects.errors import ProjectNotFound
from core.domain.reliability.reliability_service import ReliabilityService
from engines.capacity.service import DeterministicCapacityEngine
from engines.evolution.service import DeterministicEvolutionEngine
from engines.reliability.service import DeterministicReliabilityEngine
from tests.unit.architecture.world import World, make_world
from tests.unit.evolution.test_evolution_impact import WORKLOAD
from tests.unit.evolution.test_evolution_triggers import shop
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork

G = GoalType
SCALE = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(200, "requests/second"))
AVAILABLE = EvolutionGoal(G.AVAILABILITY_OBJECTIVE, target=Quantity.of("0.999", "ratio"))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def service(uow: FakeUnitOfWork, clock: FakeClock) -> EvolutionService:
    return EvolutionService(uow, DeterministicEvolutionEngine(), clock=clock)


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    return await make_world(uow, clock)


async def architecture(
    uow: FakeUnitOfWork, clock: FakeClock, world: World, *, analyzed: bool = True
) -> uuid.UUID:
    created, _ = await ArchitectureService(uow, clock=clock).create(
        project_id=world.project.id, user_id=world.ada.id, name="Shop", ir=shop()
    )
    if analyzed:
        common: dict[str, Any] = {
            "project_id": world.project.id,
            "architecture_id": created.id,
            "user_id": world.ada.id,
        }
        await CapacityService(uow, DeterministicCapacityEngine(), clock=clock).analyze(
            **common, workload=WORKLOAD
        )
        await ReliabilityService(uow, DeterministicReliabilityEngine(), clock=clock).analyze(**common)
    return created.id


async def run(service: EvolutionService, world: World, aid: uuid.UUID, **kwargs: Any) -> Any:
    kwargs.setdefault("goals", (SCALE, AVAILABLE))
    return await service.analyze(
        project_id=world.project.id,
        architecture_id=aid,
        user_id=kwargs.pop("user_id", world.ada.id),
        **kwargs,
    )


async def test_an_analysis_is_stored_with_its_evidence_and_audited(
    service: EvolutionService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    architectures = ArchitectureService(uow, clock=clock)
    common: dict[str, Any] = {"project_id": world.project.id, "architecture_id": aid, "user_id": world.ada.id}
    _, revision, _ = await architectures.get(**common)
    before = json.dumps(to_dict(revision.ir), sort_keys=True)
    report = await run(service, world, aid, label="Growth")
    a = report.analysis
    assert (a.revision_number, a.label) == (1, "Growth")
    assert a.status in {"completed", "partial"}
    stored = uow.evolution.stored[a.id]
    assert {c.rule.id for c in stored} == {"scale-replicas", "add-replica"}
    assert {e.source for e in report.evidence} >= {EvidenceSource.CAPACITY, EvidenceSource.RELIABILITY}
    assert report.inputs["impact_inputs"]["capacity"] is not None  # the workload reused for impacts
    event = uow.audit.events[-1]
    assert (event.action, event.resource_id) == (AuditAction.ARCHITECTURE_EVOLUTION_ANALYZED, aid)
    assert event.metadata["candidates"] == len(stored)
    assert report.result(stored).fingerprint == report.result_fingerprint
    after, revision, _ = await architectures.get(**common)
    assert json.dumps(to_dict(revision.ir), sort_keys=True) == before  # nothing applied
    assert after.current_revision == 1


async def test_missing_evidence_is_reported_not_invented(
    service: EvolutionService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world, analyzed=False)
    report = await run(service, world, aid)
    assert report.analysis.status == "insufficient_evidence"
    assert uow.evolution.stored[report.analysis.id] == ()
    assert {f.type.value for f in report.findings} >= {"missing_evidence", "goal_not_evaluable"}


async def test_invalid_requests_store_nothing(
    service: EvolutionService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    recorded = len(uow.audit.events)
    unknown = EvolutionGoal(G.SATISFY_REQUIREMENT, requirement_id=uuid.uuid4())
    for kwargs, reason in (
        ({"goals": (unknown,)}, "unknown_requirement"),
        ({"evidence": (EvidenceCitation(EvidenceSource.CAPACITY, uuid.uuid4()),)}, "unknown_analysis"),
    ):
        with pytest.raises(InvalidEvolutionRequest) as refused:
            await run(service, world, aid, **kwargs)
        assert refused.value.details["reason"] == reason
    assert uow.evolution.reports == {}
    assert len(uow.audit.events) == recorded


class Broken:
    def analyze(self, *args: Any) -> Any:
        raise RuntimeError("engine bug: password=hunter2")

    def catalog(self) -> dict[str, Any]:
        return {}


async def test_an_engine_failure_is_a_failed_analysis_without_internals(
    uow: FakeUnitOfWork, clock: FakeClock, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    aid = await architecture(uow, clock, world)
    with caplog.at_level(logging.ERROR, logger="architectos.evolution"):
        report = await run(EvolutionService(uow, Broken(), clock=clock), world, aid)
    assert report.analysis.status == "failed"
    assert report.analysis.error is not None
    assert "hunter2" not in report.analysis.error.message + caplog.text
    assert [r.error_type for r in caplog.records] == ["RuntimeError"]  # type: ignore[attr-defined]


async def test_access_and_archived_architectures(
    service: EvolutionService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await run(service, world, aid)
    with pytest.raises(PermissionDenied):
        await run(service, world, aid, user_id=world.vic.id)
    common: dict[str, Any] = {
        "project_id": world.project.id,
        "architecture_id": aid,
        "analysis_id": report.analysis.id,
    }
    assert (await service.get(**common, user_id=world.vic.id)).analysis.id == report.analysis.id
    with pytest.raises(ProjectNotFound):
        await service.get(**common, user_id=world.eve.id)
    with pytest.raises(ArchitectureNotFound):
        await service.get(**(common | {"project_id": world.other.id}), user_id=world.ada.id)
    with pytest.raises(EvolutionAnalysisNotFound):
        await service.get(**(common | {"analysis_id": uuid.uuid4()}), user_id=world.ada.id)
    architectures = ArchitectureService(uow, clock=clock)
    await architectures.archive(project_id=world.project.id, architecture_id=aid, user_id=world.ada.id)
    with pytest.raises(ArchitectureArchived):
        await run(service, world, aid)


async def test_reading_candidates_their_diff_and_the_alternatives(
    service: EvolutionService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    ids = []
    for _ in range(2):
        ids.append((await run(service, world, aid)).analysis.id)
        clock.advance(timedelta(seconds=1))
    page = await service.list_analyses(
        project_id=world.project.id, architecture_id=aid, user_id=world.vic.id, limit=1
    )
    assert [r.analysis.id for r in page.items] == [ids[1]]
    assert page.next_cursor is not None
    common: dict[str, Any] = {"project_id": world.project.id, "architecture_id": aid, "analysis_id": ids[0]}
    first = await service.list_candidates(**common, user_id=world.ada.id, query=CandidateQuery(limit=1))
    rest = await service.list_candidates(
        **common, user_id=world.ada.id, query=CandidateQuery(limit=50), cursor=first.next_cursor
    )
    everything = [*first.items, *rest.items]
    assert everything == list(uow.evolution.stored[ids[0]])  # canonical order, paged
    scaling = await service.list_candidates(
        **common, user_id=world.ada.id, query=CandidateQuery(category=CandidateCategory.SCALING)
    )
    assert {c.category for c in scaling.items} == {CandidateCategory.SCALING}
    candidate, overlay = await service.get_candidate(
        **common, candidate_id=everything[0].id, user_id=world.ada.id
    )
    assert candidate == everything[0]
    assert overlay is not None
    assert overlay["candidate_id"] == candidate.id
    assert overlay["changes"]
    with pytest.raises(CandidateNotFound):
        await service.get_candidate(**common, candidate_id="evo_" + "0" * 20, user_id=world.ada.id)
    found = await service.alternatives(**common, user_id=world.vic.id)
    assert {a.goal for a in found} == {SCALE.key, AVAILABLE.key}


async def test_the_same_inputs_give_the_same_result(
    service: EvolutionService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    first, second = await run(service, world, aid), await run(service, world, aid)
    assert first.analysis.id != second.analysis.id
    assert first.result_fingerprint == second.result_fingerprint
    assert [c.id for c in uow.evolution.stored[first.analysis.id]] == [
        c.id for c in uow.evolution.stored[second.analysis.id]
    ]


# --- decisions ------------------------------------------------------------------------------------


async def test_a_decision_is_drafted_decided_and_linked_by_people(
    service: EvolutionService, uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    aid = await architecture(uow, clock, world)
    report = await run(service, world, aid)
    decisions = DecisionService(uow, clock=clock)
    common: dict[str, Any] = {"project_id": world.project.id, "user_id": world.ada.id}
    draft = await decisions.draft(**common, architecture_id=aid, analysis_id=report.analysis.id)
    assert (draft.status, draft.reference) == (DecisionStatus.PROPOSED, "ADR-1")
    assert uow.audit.events[-1].action is AuditAction.DECISION_PROPOSED
    second = await decisions.draft(**common, architecture_id=aid, analysis_id=report.analysis.id)
    assert second.reference == "ADR-2"
    chosen = draft.options[0].candidate_id
    with pytest.raises(PermissionDenied):
        await decisions.accept(
            project_id=world.project.id, user_id=world.vic.id, decision_id=draft.id, candidate_id=chosen,
            rationale="Viewers read.",
        )  # fmt: skip
    accepted = await decisions.accept(**common, decision_id=draft.id, candidate_id=chosen, rationale="Fits.")
    assert (accepted.status, accepted.decided_by_user_id) == (DecisionStatus.ACCEPTED, world.ada.id)
    arch, _, _ = await ArchitectureService(uow, clock=clock).get(
        project_id=world.project.id, architecture_id=aid, user_id=world.ada.id
    )
    assert arch.current_revision == 1  # accepting applied nothing
    with pytest.raises(InvalidDecision):
        await decisions.link_revision(
            **common, decision_id=draft.id, revision_number=1
        )  # not after the baseline
    with pytest.raises(InvalidDecision):
        await decisions.supersede(**common, decision_id=draft.id, by_decision_id=second.id)  # not accepted
    await decisions.reject(**common, decision_id=second.id, rationale="Not now.")
    with pytest.raises(InvalidDecisionTransition):
        await decisions.accept(**common, decision_id=second.id, candidate_id=chosen, rationale="Later.")
    document = await decisions.document(**common, decision_id=draft.id)
    assert "Accepted:" in document
    assert "Not linked." in document
    listed = await decisions.list_decisions(project_id=world.project.id, user_id=world.vic.id)
    assert [d.reference for d in listed.items] == ["ADR-1", "ADR-2"]
    with pytest.raises(DecisionNotFound):
        await decisions.get(project_id=world.other.id, decision_id=draft.id, user_id=world.ada.id)
    with pytest.raises(ProjectNotFound):
        await decisions.get(project_id=world.project.id, decision_id=draft.id, user_id=world.eve.id)
    actions = [e.action for e in uow.audit.events if e.resource_type == "decision"]
    assert actions == [
        AuditAction.DECISION_PROPOSED, AuditAction.DECISION_PROPOSED, AuditAction.DECISION_ACCEPTED,
        AuditAction.DECISION_REJECTED,
    ]  # fmt: skip
