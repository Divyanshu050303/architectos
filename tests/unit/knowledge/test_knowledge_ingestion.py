"""The ingestion and indexing lifecycle (Knowledge/RAG Engine, phase 4): a first reading indexes version 1;
unchanged content creates nothing; changed content indexes version n + 1, keeping the ids of what did
not change; a failure — at any stage — leaves the last known-good version in force (stale if it was);
record snapshots go stale when their record reads differently, and are current again once re-read;
an input that does not belong to the source, or a source that cannot be ingested, is refused first."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from core.domain.decisions.entities import Decision, DecisionOption, DecisionStatus
from core.domain.knowledge.errors import InvalidKnowledgeRequest, InvalidKnowledgeTransition
from core.domain.knowledge.ingestion import IngestionRun
from core.domain.knowledge.ports import IngestionInput, IngestionOutcome
from core.domain.knowledge.sources import KnowledgeSource, RecordRef
from core.domain.knowledge.uploads import DocumentUpload
from core.domain.knowledge.values import IndexStatus, IngestionStatus, SourceType, Stage, Trigger
from engines.knowledge.chunking import ChunkingConfig
from engines.knowledge.engine import DeterministicKnowledgeEngine
from tests.unit.requirements.test_planning_input import PROJECT, requirement

AT = datetime(2026, 10, 2, tzinfo=UTC)
USER, SOURCE = uuid.UUID(int=2), uuid.UUID(int=3)
ENGINE = DeterministicKnowledgeEngine()
PATH = "docs/runbook.md"
RUNBOOK = (
    "# Runbook\n\nOrders service.\n\n## Failover\n\nPromote the replica within 30 s.\n\n"
    "## Restore\n\nRestore nightly.\n"
)


def upload_source() -> KnowledgeSource:
    return KnowledgeSource(SOURCE, PROJECT.id, SourceType.MARKDOWN, "Runbook", USER, AT, AT, path=PATH)


def run(n: int = 1, trigger: Trigger = Trigger.REGISTER) -> IngestionRun:
    return IngestionRun(uuid.UUID(int=100 + n), PROJECT.id, SOURCE, trigger, USER, AT)


def ingest(
    source: KnowledgeSource,
    content: str | IngestionInput,
    n: int = 1,
    engine: DeterministicKnowledgeEngine = ENGINE,
) -> IngestionOutcome:
    given = (
        content
        if isinstance(content, IngestionInput)
        else IngestionInput(upload=DocumentUpload(PATH, content))
    )
    return engine.ingest(source, given, run(n), AT + timedelta(minutes=n))


def test_a_first_reading_indexes_version_one_in_full() -> None:
    outcome = ingest(upload_source(), RUNBOOK)
    source, ended, indexed = outcome.source, outcome.run, outcome.indexed
    assert indexed is not None
    assert (source.status, source.indexed_version, source.indexed_checksum) == (
        IndexStatus.INDEXED, 1, indexed.version.checksum,
    )  # fmt: skip
    assert (ended.status, ended.stage, ended.indexed_version, ended.checksum) == (
        IngestionStatus.COMPLETED, Stage.DONE, 1, indexed.version.checksum,
    )  # fmt: skip
    assert ended.counts.to_dict() == {"documents": 1, "chunks": 3, "skipped": 0, "failed": 0}
    version = indexed.version
    assert (version.number, version.chunks, version.ingestion_run_id) == (1, 3, ended.id)
    assert {c.source_version for c in indexed.chunks} == {1}
    assert ended.versions == ENGINE.versions(SourceType.MARKDOWN) == indexed.version.versions
    assert ended.versions["chunk-max-chars"] == 1500


def test_unchanged_content_creates_nothing() -> None:
    first = ingest(upload_source(), RUNBOOK)
    again = ingest(first.source, RUNBOOK.replace("\n", "\r\n"), 2)  # line endings are not content
    assert again.indexed is None
    assert (again.run.status, again.run.indexed_version, again.run.counts.chunks) == (
        IngestionStatus.UNCHANGED, None, 0,
    )  # fmt: skip
    assert (again.source.indexed_version, again.source.status) == (1, IndexStatus.INDEXED)


def test_changed_content_indexes_the_next_version_keeping_unchanged_ids() -> None:
    first = ingest(upload_source(), RUNBOOK)
    second = ingest(first.source, RUNBOOK.replace("30 s", "60 s"), 2)
    assert first.indexed is not None
    assert second.indexed is not None
    assert (second.source.indexed_version, second.indexed.version.number) == (2, 2)
    before = {c.locator.heading_path: c.id for c in first.indexed.chunks}
    after = {c.locator.heading_path: c.id for c in second.indexed.chunks}
    assert before[("Runbook", "Restore")] == after[("Runbook", "Restore")]
    assert before[("Runbook", "Failover")] != after[("Runbook", "Failover")]
    assert {c.source_version for c in second.indexed.chunks} == {2}  # stated with every passage
    with_warning = ingest(second.source, RUNBOOK + "\n## Empty\n\npassword: hunter2\n", 3)
    assert with_warning.run.status is IngestionStatus.COMPLETED_WITH_WARNINGS
    assert len(with_warning.run.warnings) == 1
    assert "hunter2" not in repr(with_warning)


def test_a_failure_keeps_the_last_known_good_version() -> None:
    never = ingest(upload_source(), "# Only\n\n## Headings\n")
    assert never.indexed is None
    assert (never.source.status, never.source.indexed_version) == (IndexStatus.FAILED, None)
    assert (never.run.status, never.run.stage, never.run.errors[0].code) == (
        IngestionStatus.FAILED, Stage.CHUNKING, "nothing_to_index",
    )  # fmt: skip
    indexed = ingest(never.source, RUNBOOK, 2)
    assert indexed.source.indexed_version == 1  # a failed source can be ingested again
    broken = ingest(indexed.source, "bad " + chr(0) + " byte", 3)
    assert (broken.run.status, broken.run.stage, broken.run.errors[0].code) == (
        IngestionStatus.FAILED, Stage.EXTRACTING, "invalid_characters",
    )  # fmt: skip
    assert broken.indexed is None
    assert (broken.source.status, broken.source.indexed_version, broken.source.indexed_checksum) == (
        IndexStatus.INDEXED, 1, indexed.source.indexed_checksum,
    )  # fmt: skip
    assert broken.run.checksum is None  # nothing was read far enough to have one


def test_inputs_must_belong_to_the_source_and_the_source_must_be_ingestible() -> None:
    source = upload_source()
    with pytest.raises(InvalidKnowledgeRequest) as other:
        ENGINE.ingest(source, IngestionInput(upload=DocumentUpload("docs/other.md", RUNBOOK)), run(), AT)
    assert other.value.details == {"field": "content.path", "reason": "another_document"}
    with pytest.raises(InvalidKnowledgeRequest):
        ENGINE.ingest(source, IngestionInput(upload=DocumentUpload("docs/runbook.txt", RUNBOOK)), run(), AT)
    with pytest.raises(InvalidKnowledgeRequest):
        IngestionInput()  # exactly one input
    archived = ingest(source, RUNBOOK).source.archive(USER, AT)
    with pytest.raises(InvalidKnowledgeTransition):
        ingest(archived, RUNBOOK, 2)
    with pytest.raises(InvalidKnowledgeTransition):
        ingest(source.begin_ingestion(AT), RUNBOOK)  # one ingestion at a time


def decision(status: DecisionStatus = DecisionStatus.PROPOSED, **changes: object) -> Decision:
    option = DecisionOption("evo_a", "Read replicas", "scaling", ("db.replicas = 3",), "valid", (), ())
    found = Decision(
        uuid.UUID(int=30), PROJECT.id, uuid.UUID(int=31), 3, "Scale order reads", status,
        "Reads saturate the primary.", (option,), None, AT,
    )  # fmt: skip
    return replace(found, **changes)  # type: ignore[arg-type]


def adr_source() -> KnowledgeSource:
    record = RecordRef(SourceType.DECISION, uuid.UUID(int=30), "ADR-3")
    return KnowledgeSource(SOURCE, PROJECT.id, SourceType.DECISION, "ADR-3", USER, AT, AT, record=record)


def test_a_record_snapshot_goes_stale_and_is_current_once_re_read() -> None:
    proposed = IngestionInput(decision=decision())
    indexed = ingest(adr_source(), proposed).source
    assert indexed.indexed_version == 1
    assert not ENGINE.snapshot_changed(indexed, proposed)
    accepted = IngestionInput(
        decision=decision(DecisionStatus.ACCEPTED, chosen_option="evo_a", rationale="Cheapest.")
    )
    assert ENGINE.snapshot_changed(indexed, accepted)
    reread = ingest(indexed.stale(AT), accepted, 2)
    assert (reread.source.status, reread.source.indexed_version) == (IndexStatus.INDEXED, 2)
    assert not ENGINE.snapshot_changed(reread.source, accepted)
    with pytest.raises(InvalidKnowledgeRequest):
        ENGINE.snapshot_changed(indexed, IngestionInput(decision=decision(id=uuid.UUID(int=99))))


def test_a_requirement_snapshot_follows_its_versions_and_its_deletion() -> None:
    v1 = requirement(12, version=1)
    record = RecordRef(SourceType.REQUIREMENT, v1.id, "REQ-12", 1)
    source = KnowledgeSource(
        SOURCE, PROJECT.id, SourceType.REQUIREMENT, "REQ-12", USER, AT, AT, record=record
    )
    indexed = ingest(source, IngestionInput(requirement=v1)).source
    same_words = ingest(indexed, IngestionInput(requirement=replace(v1, version=2)), 2)
    assert same_words.run.status is IngestionStatus.UNCHANGED  # a new version with the same content
    assert same_words.source.record is not None
    assert same_words.source.record.version == 2  # the version read is recorded
    deleted = replace(v1, version=3, deleted_at=AT)
    assert ENGINE.snapshot_changed(same_words.source, IngestionInput(requirement=deleted))
    gone = ingest(same_words.source.stale(AT), IngestionInput(requirement=deleted), 3)
    assert (gone.run.status, gone.run.errors[0].code) == (IngestionStatus.FAILED, "record_deleted")
    assert (gone.source.status, gone.source.indexed_version) == (
        IndexStatus.STALE,
        1,
    )  # still said to be stale
    foreign = replace(v1, project_id=uuid.UUID(int=77))
    with pytest.raises(InvalidKnowledgeRequest):
        ingest(indexed, IngestionInput(requirement=foreign), 4)


def test_ingestion_is_deterministic_and_configurable() -> None:
    first, again = ingest(upload_source(), RUNBOOK), ingest(upload_source(), RUNBOOK)
    assert first == again
    small = DeterministicKnowledgeEngine(ChunkingConfig(200))
    outcome = ingest(upload_source(), RUNBOOK, engine=small)
    assert outcome.run.versions["chunk-max-chars"] == 200
    assert ENGINE.versions(SourceType.REQUIREMENT) == {
        "requirement": 1, "knowledge-redaction": 1, "knowledge-structure": 1, "fields": 1,
        "knowledge-identifiers": 1, "chunk-max-chars": 1500,
    }  # fmt: skip
