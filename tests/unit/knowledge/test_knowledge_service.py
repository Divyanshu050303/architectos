"""Knowledge use cases (Knowledge/RAG Engine, phase 6) on the in-memory unit of work: the run, the version
and the source stored together (the run first, the source last); a record deleted since its snapshot
is a failed ingestion that keeps the version in force; staleness found during retrieval; retries of
failed runs only; audit and metrics carry no content; the service is the KnowledgeRetriever."""

import uuid
from dataclasses import replace

import pytest

from core.domain.audit.entities import AuditAction
from core.domain.decisions.entities import Decision, DecisionOption, DecisionStatus
from core.domain.knowledge.errors import (
    InvalidKnowledgeRequest,
    KnowledgeSourceExists,
    KnowledgeSourceNotFound,
)
from core.domain.knowledge.knowledge_service import KnowledgeService, SourceRegistration
from core.domain.knowledge.ports import KnowledgeRetriever
from core.domain.knowledge.retrieval import RetrievalQuery
from core.domain.knowledge.uploads import DocumentUpload
from core.domain.knowledge.values import IndexStatus, IngestionStatus, SourceType
from core.domain.organizations.errors import PermissionDenied
from engines.knowledge.engine import DeterministicKnowledgeEngine
from tests.unit.architecture.world import World, make_world
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork
from tests.unit.requirements.test_planning_input import requirement
from tests.unit.requirements.test_requirement_analysis_service import RecordingMetrics

NOW = FakeClock().now
SECRET = "hunter2"
RUNBOOK = f"# Runbook\n\nPromote the read replica on failover.\n\napi_key = {SECRET}\n"


class Env:
    def __init__(self, uow: FakeUnitOfWork, world: World, metrics: RecordingMetrics) -> None:
        self.uow, self.world, self.metrics = uow, world, metrics
        self.service = KnowledgeService(
            uow, DeterministicKnowledgeEngine(), clock=FakeClock(), metrics=metrics
        )

    @property
    def project(self) -> uuid.UUID:
        return self.world.project.id

    @property
    def ada(self) -> uuid.UUID:
        return self.world.ada.id


@pytest.fixture
async def env() -> Env:
    clock = FakeClock()
    uow = FakeUnitOfWork(clock)
    return Env(uow, await make_world(uow, clock), RecordingMetrics())


def upload(content: str = RUNBOOK, path: str = "docs/runbook.md") -> SourceRegistration:
    return SourceRegistration(DocumentUpload(path, content))


def decision(project: uuid.UUID) -> Decision:
    option = DecisionOption("evo_a", "Read replicas", "scaling", ("db.replicas = 3",), "valid", (), ())
    return Decision(
        uuid.UUID(int=30), project, uuid.UUID(int=31), 3, "Scale order reads", DecisionStatus.PROPOSED,
        "Reads saturate the primary.", (option,), None, NOW,
    )  # fmt: skip


async def test_registration_stores_the_run_then_the_version_then_the_source(env: Env) -> None:
    outcome = await env.service.register(project_id=env.project, user_id=env.ada, registration=upload())
    stored = env.uow.knowledge
    assert stored.sources[outcome.source.id].indexed_version == 1
    assert stored.runs[outcome.run.id].status is IngestionStatus.COMPLETED_WITH_WARNINGS
    assert (outcome.source.id, 1) in stored.versions  # the fake refuses a version before its run
    knowledge = [e for e in env.uow.audit.events if e.action.value.startswith("knowledge_")]
    assert [e.action for e in knowledge] == [
        AuditAction.KNOWLEDGE_SOURCE_REGISTERED,
        AuditAction.KNOWLEDGE_SOURCE_INGESTED,
    ]
    audited = repr(knowledge)
    assert SECRET not in audited
    assert "runbook" not in audited  # neither the name nor the path
    labels = [labels for _, _, labels in env.metrics.events]
    assert {"status": "completed_with_warnings", "source_type": "markdown"} in labels
    with pytest.raises(KnowledgeSourceExists) as twice:
        await env.service.register(project_id=env.project, user_id=env.ada, registration=upload())
    assert twice.value.details == {"source_id": str(outcome.source.id)}
    with pytest.raises(PermissionDenied):
        await env.service.register(
            project_id=env.project, user_id=env.world.vic.id, registration=upload("# X\n\ny", "x.md")
        )


async def test_a_deleted_record_is_a_failed_ingestion_that_keeps_the_version(env: Env) -> None:
    found = replace(requirement(12), project_id=env.project)
    env.uow.requirements.by_id[found.id] = found
    registration = SourceRegistration(record_type=SourceType.REQUIREMENT, record_id=found.id)
    source = (
        await env.service.register(project_id=env.project, user_id=env.ada, registration=registration)
    ).source
    env.uow.requirements.by_id[found.id] = replace(found, deleted_at=NOW)
    read = await env.service.get(project_id=env.project, source_id=source.id, user_id=env.ada)
    assert read.status is IndexStatus.STALE  # a record gone is a snapshot out of date
    gone = await env.service.reindex(project_id=env.project, source_id=source.id, user_id=env.ada)
    assert (gone.run.status, gone.run.errors[0].code) == (IngestionStatus.FAILED, "record_deleted")
    assert (gone.source.status, gone.source.indexed_version) == (IndexStatus.STALE, 1)
    succeeded = next(r.id for r in env.uow.knowledge.runs.values() if r.status is not IngestionStatus.FAILED)
    with pytest.raises(InvalidKnowledgeRequest):  # only a failed run is retried
        await env.service.reindex(
            project_id=env.project, source_id=source.id, user_id=env.ada, retry_of=succeeded
        )


async def test_retrieval_finds_staleness_and_never_leaves_the_project(env: Env) -> None:
    proposed = decision(env.project)
    env.uow.decisions.decisions[proposed.id] = proposed
    registration = SourceRegistration(record_type=SourceType.DECISION, record_id=proposed.id)
    await env.service.register(project_id=env.project, user_id=env.ada, registration=registration)
    await env.service.register(project_id=env.world.other.id, user_id=env.ada, registration=upload())
    retriever: KnowledgeRetriever = env.service  # the boundary consumers use
    query = RetrievalQuery("reads saturate primary")
    fresh = await retriever.retrieve(project_id=env.project, user_id=env.ada, query=query)
    assert fresh.passages
    assert not any(p.stale for p in fresh.passages)
    assert {p.citation.source_name for p in fresh.passages} == {"ADR-3"}  # never the other project's
    env.uow.decisions.decisions[proposed.id] = replace(
        proposed, context="Reads saturate the primary at peak."
    )
    stale = await retriever.retrieve(project_id=env.project, user_id=env.ada, query=query)
    assert stale.passages
    assert all(p.stale for p in stale.passages)
    mine = [s.status for s in env.uow.knowledge.sources.values() if s.project_id == env.project]
    assert mine == [IndexStatus.STALE]
    excluded = await retriever.retrieve(
        project_id=env.project,
        user_id=env.ada,
        query=RetrievalQuery("reads saturate primary", include_stale=False),
    )
    assert excluded.insufficient_evidence
    assert "1 stale source(s) were excluded, as asked." in excluded.limitations
    assert {"outcome": "insufficient_evidence"} in [labels for _, _, labels in env.metrics.events]


async def test_sources_of_another_project_are_not_found(env: Env) -> None:
    theirs = await env.service.register(project_id=env.world.other.id, user_id=env.ada, registration=upload())
    with pytest.raises(KnowledgeSourceNotFound):
        await env.service.get(project_id=env.project, source_id=theirs.source.id, user_id=env.ada)
    with pytest.raises(KnowledgeSourceNotFound):
        await env.service.reindex(
            project_id=env.project, source_id=theirs.source.id, user_id=env.ada, content=RUNBOOK
        )
