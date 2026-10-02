"""Knowledge domain contracts (Knowledge/RAG Engine, phase 1): typed, validated sources with exact
versions and a last known-good version that survives failure; documents and chunks with stable ids
and locators that claim only what a source supports; ingestion runs that end exactly one way;
queries that cannot widen their scope; results that are evidence, never answers."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from core.domain.knowledge.documents import KnowledgeChunk, KnowledgeDocument, Locator
from core.domain.knowledge.errors import (
    InvalidKnowledgeRecord,
    InvalidKnowledgeRequest,
    InvalidKnowledgeTransition,
)
from core.domain.knowledge.ingestion import Counts, IngestionError, IngestionRun
from core.domain.knowledge.retrieval import (
    MAX_RESULTS,
    Citation,
    Passage,
    RetrievalQuery,
    RetrievalResult,
)
from core.domain.knowledge.sources import KnowledgeSource, RecordRef, SourceVersion
from core.domain.knowledge.values import (
    ContentType,
    IndexStatus,
    IngestionStatus,
    Lifecycle,
    RetrievalMethod,
    SourceType,
    Stage,
    Trigger,
    Verification,
    fingerprint,
)

AT = datetime(2026, 10, 2, tzinfo=UTC)
LATER = AT + timedelta(minutes=5)
PROJECT, USER, SOURCE = uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3)
SUM_A, SUM_B = fingerprint("a"), fingerprint("b")
STRATEGY = "markdown-sections@1"


def upload(**changes: object) -> KnowledgeSource:
    source = KnowledgeSource(
        SOURCE, PROJECT, SourceType.MARKDOWN, "Runbook", USER, AT, AT, path="docs/runbook.md"
    )
    return replace(source, **changes)  # type: ignore[arg-type]


def adr() -> KnowledgeSource:
    record = RecordRef(SourceType.DECISION, uuid.UUID(int=9), "ADR-3")
    return KnowledgeSource(SOURCE, PROJECT, SourceType.DECISION, "ADR-3", USER, AT, AT, record=record)


def chunk(text: str = "Fail over to the replica within 30 s.", **changes: object) -> KnowledgeChunk:
    found = KnowledgeChunk(SOURCE, 1, "kdo_x", 0, text, Locator(("Runbook", "Failover"), 12, 14), STRATEGY)
    return replace(found, **changes)  # type: ignore[arg-type]


def test_a_source_is_an_upload_or_a_record_snapshot_of_one_project() -> None:
    source = upload()
    assert (source.content_type, source.status, source.retrievable) == (
        ContentType.MARKDOWN, IndexStatus.PENDING, False,
    )  # fmt: skip
    assert adr().content_type is ContentType.RECORD
    with pytest.raises(InvalidKnowledgeRequest):  # a record needs its snapshot reference
        KnowledgeSource(SOURCE, PROJECT, SourceType.DECISION, "ADR-3", USER, AT, AT)
    with pytest.raises(InvalidKnowledgeRequest):  # an upload names its path, a record does not
        replace(adr(), path="x.md")
    with pytest.raises(InvalidKnowledgeRequest):  # a requirement snapshot names its version
        RecordRef(SourceType.REQUIREMENT, uuid.UUID(int=9), "REQ-1")
    assert RecordRef(SourceType.REQUIREMENT, uuid.UUID(int=9), "REQ-1", 4).to_dict()["version"] == 4
    with pytest.raises(InvalidKnowledgeRecord):  # metadata holds short identifiers, never content
        upload(metadata={"Bad Key": "x"})
    assert "project_id" in source.to_dict()


def test_the_last_known_good_version_survives_a_failed_ingestion() -> None:
    source = upload().begin_ingestion(AT)
    assert source.status is IndexStatus.PROCESSING
    with pytest.raises(InvalidKnowledgeTransition):  # one ingestion at a time
        source.begin_ingestion(AT)
    assert source.ingestion_failed(LATER).status is IndexStatus.FAILED  # never indexed: failed
    indexed = source.indexed(1, SUM_A, LATER)
    assert (indexed.status, indexed.indexed_version, indexed.retrievable) == (IndexStatus.INDEXED, 1, True)
    with pytest.raises(InvalidKnowledgeTransition):  # versions are numbered without gaps
        indexed.begin_ingestion(LATER).indexed(3, SUM_B, LATER)
    failed = indexed.begin_ingestion(LATER).ingestion_failed(LATER)
    assert (failed.status, failed.indexed_version, failed.indexed_checksum) == (IndexStatus.INDEXED, 1, SUM_A)
    assert indexed.begin_ingestion(LATER).unchanged(LATER).indexed_version == 1
    with pytest.raises(InvalidKnowledgeTransition):
        upload().unchanged(LATER)  # nothing indexed yet: not "unchanged"


def test_a_record_snapshot_becomes_stale_and_archived_sources_are_never_ingested() -> None:
    with pytest.raises(InvalidKnowledgeTransition):
        adr().stale(LATER)  # nothing indexed: nothing to be out of date
    stale = adr().begin_ingestion(AT).indexed(1, SUM_A, AT).stale(LATER)
    assert (stale.status, stale.retrievable) == (IndexStatus.STALE, True)  # still evidence, marked
    with pytest.raises(InvalidKnowledgeTransition):
        upload().begin_ingestion(AT).indexed(1, SUM_A, AT).stale(LATER)  # uploads do not go stale
    with pytest.raises(InvalidKnowledgeRequest):
        adr().with_record(RecordRef(SourceType.DECISION, uuid.UUID(int=10), "ADR-4"))
    archived = stale.archive(USER, LATER)
    assert (archived.lifecycle, archived.retrievable, archived.indexed_version) == (
        Lifecycle.ARCHIVED, False, 1,
    )  # fmt: skip
    with pytest.raises(InvalidKnowledgeTransition):
        archived.begin_ingestion(LATER)
    with pytest.raises(InvalidKnowledgeTransition):
        archived.archive(USER, LATER)


def test_a_version_records_what_produced_it() -> None:
    version = SourceVersion(SOURCE, 1, SUM_A, uuid.UUID(int=5), AT, 1, 4, {"markdown": 1, "chunker": 1})
    assert version.chunks == 4
    with pytest.raises(InvalidKnowledgeRecord):
        SourceVersion(SOURCE, 1, SUM_A, uuid.UUID(int=5), AT, 1, 4, {})  # versions are required
    with pytest.raises(InvalidKnowledgeRecord):
        SourceVersion(SOURCE, 1, "not-a-checksum", uuid.UUID(int=5), AT, 1, 4, {"markdown": 1})


def test_locators_claim_only_what_the_source_supports() -> None:
    lines = Locator(("Runbook", "Failover"), 12, 30)
    assert lines.reference() == "Runbook > Failover (lines 12-30)"
    record = Locator(record="ADR-3", field="context")
    assert (record.reference(), record.line_start, record.heading_path) == ("ADR-3 context", None, ())
    assert Locator(("Notes",), 4, 4).reference() == "Notes (line 4)"
    for bad in (
        {"line_start": 3},  # a start without an end
        {"line_start": 9, "line_end": 2},
        {"line_start": 0, "line_end": 1},
        {"heading_path": tuple(str(i) for i in range(7))},
    ):
        with pytest.raises(InvalidKnowledgeRecord):
            Locator(**bad)
    with pytest.raises(InvalidKnowledgeRecord):
        Locator()  # a passage always has a location — or it is not indexed


def test_chunk_and_document_ids_are_stable_while_the_content_is() -> None:
    first = chunk()
    assert first.id == chunk(source_version=2, sequence=5, document_id="kdo_y").id  # same words, same place
    assert first.id != chunk("Fail over to the replica within 60 s.").id  # changed words
    assert first.id != chunk(occurrence=1).id  # the same passage repeated is another passage
    assert first.id != chunk(locator=Locator(("Runbook", "Restore"), 12, 14)).id  # another section
    assert first.id != chunk(strategy="markdown-sections@2").id  # another strategy
    document = KnowledgeDocument(SOURCE, 1, ContentType.MARKDOWN, SUM_A, "docs/runbook.md", "Runbook")
    assert document.id == replace(document, source_version=2).id
    assert document.id != replace(document, checksum=SUM_B).id
    assert document.verification is Verification.USER_PROVIDED  # retrieval verifies nothing


def test_chunks_keep_the_words_and_name_their_identifiers() -> None:
    words = "Keep `max_connections = 200` (ADR-3, REQ-12) — 1.5 GiB per node."
    found = chunk(words, identifiers=("REQ-12", "ADR-3", "ADR-3"))
    assert found.text == words  # never rewritten: numbers, units and code as written
    assert found.identifiers == ("ADR-3", "REQ-12")
    assert found.to_dict()["checksum"] == found.checksum
    for bad in ({"text": ""}, {"text": "x" * 4001}, {"strategy": "sections"}, {"sequence": -1}):
        with pytest.raises(InvalidKnowledgeRecord):
            chunk(**bad)


def run() -> IngestionRun:
    return IngestionRun(uuid.UUID(int=7), PROJECT, SOURCE, Trigger.REGISTER, USER, AT)


def test_an_ingestion_ends_exactly_one_way() -> None:
    versions = {"markdown": 1, "normalizer": 1, "markdown-sections": 1}
    running = run().start(AT, versions).at_stage(Stage.CHUNKING, SUM_A)
    done = running.complete(1, Counts(documents=1, chunks=3), LATER)
    assert (done.status, done.indexed_version, done.stage) == (IngestionStatus.COMPLETED, 1, Stage.DONE)
    warned = running.complete(1, Counts(1, 2, skipped=1), LATER, ("1 empty section was skipped.",))
    assert warned.status is IngestionStatus.COMPLETED_WITH_WARNINGS
    same = running.unchanged(Counts(documents=1), LATER)
    assert (same.status, same.indexed_version) == (IngestionStatus.UNCHANGED, None)  # nothing created
    error = IngestionError("invalid_encoding", "The document is not valid UTF-8.", Stage.EXTRACTING)
    failed = running.fail((error,), LATER)
    assert (failed.status, failed.indexed_version, failed.stage) == (
        IngestionStatus.FAILED, None, Stage.CHUNKING,
    )  # fmt: skip
    with pytest.raises(InvalidKnowledgeTransition):
        run().complete(1, Counts(), LATER)  # never started
    with pytest.raises(InvalidKnowledgeTransition):
        done.fail((error,), LATER)  # already ended
    with pytest.raises(InvalidKnowledgeRecord):
        running.fail((), LATER)  # a failure says why
    with pytest.raises(InvalidKnowledgeRecord):
        IngestionError("Invalid Code", "x", Stage.EXTRACTING)
    assert "text" not in str(failed.to_dict())  # errors are codes and messages, never content


def test_a_query_narrows_its_scope_and_never_asks_for_everything() -> None:
    query = RetrievalQuery("  failover \n  replica ", identifiers=("ADR-3", "ADR-3"))
    assert (query.text, query.identifiers, query.limit) == ("failover replica", ("ADR-3",), 10)
    assert not hasattr(query, "project_id")  # the project is the caller's, resolved by the service
    for bad in (
        {"text": "   "},  # nothing to find
        {"text": "x" * 501},
        {"text": "drop\x00table"},
        {"text": "x", "limit": MAX_RESULTS + 1},
        {"text": "x", "limit": 0},
        {"identifiers": ("not an id",)},
        {"text": "x", "source_ids": ("not-a-uuid",)},
        {"text": "x", "source_types": ("pdf",)},
    ):
        with pytest.raises(InvalidKnowledgeRequest):
            RetrievalQuery(**bad)


def passage(rank: int, chunk_id: str = "kch_a") -> Passage:
    citation = Citation(
        SOURCE, "Runbook", SourceType.MARKDOWN, 2, "kdo_x", chunk_id, Locator(("Failover",), 3, 9)
    )
    return Passage(
        citation, "Fail over within 30 s.", RetrievalMethod.LEXICAL, rank, Verification.USER_PROVIDED
    )


def test_a_result_is_evidence_with_citations_never_padded() -> None:
    found = RetrievalResult((passage(1), passage(2, "kch_b")), searched_sources=3)
    assert not found.insufficient_evidence
    shown = found.to_dict()
    assert shown["passages"][0]["citation"]["reference"] == "Runbook (v2): Failover (lines 3-9)"
    assert not {"score", "similarity", "embedding", "confidence"} & set(shown["passages"][0])
    empty = RetrievalResult((), searched_sources=3)
    assert (empty.insufficient_evidence, empty.to_dict()["passages"]) == (True, [])
    with pytest.raises(InvalidKnowledgeRecord):  # ranks are an order, without gaps
        RetrievalResult((passage(2),), searched_sources=1)
    with pytest.raises(InvalidKnowledgeRecord):  # the same passage is never returned twice
        RetrievalResult((passage(1), passage(2)), searched_sources=1)
