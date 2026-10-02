"""The deterministic knowledge engine: one ingestion, start to end — read the input with its source's
adapter, redact, compare the checksum with the indexed version, structure and chunk, and say how it
ended. Pure: it stores, fetches and executes nothing; the same inputs give the same outcome.

How an ingestion ends:

- **unchanged** — the content's checksum is the indexed version's: no version, document or passage
  is created; the source is current again (a stale snapshot whose record reads the same is no
  longer stale).
- **completed** (or **completed with warnings**: something redacted, a heading-only section skipped)
  — version ``n + 1`` with its document and passages, in full; only then does the source point at it.
- **failed** — the run records the stage it stopped at and why (codes and lines, never content); the
  source keeps its last known-good version, stale if it was, or is ``failed`` if it never had one.

An input that does not belong to the source (another document's path, another record) is refused
before anything starts; so is a source that is archived or already being ingested.
"""

from datetime import datetime

from core.domain.knowledge.errors import InvalidKnowledgeRequest
from core.domain.knowledge.ingestion import Counts, IngestionRun
from core.domain.knowledge.ports import Indexed, IngestionInput, IngestionOutcome
from core.domain.knowledge.sources import KnowledgeSource, SourceVersion
from core.domain.knowledge.values import SourceType, Stage

from . import redaction, structure
from .adapters import (
    VERSIONS,
    Extraction,
    ExtractionFailed,
    extract_decision,
    extract_requirement,
    extract_upload,
)
from .chunking import IDENTIFIERS, STRATEGIES, ChunkingConfig, chunk

ADAPTERS = {
    SourceType.MARKDOWN: "markdown",
    SourceType.TEXT: "text",
    SourceType.DECISION: "decision",
    SourceType.REQUIREMENT: "requirement",
}


def _refuse(field_name: str, reason: str) -> InvalidKnowledgeRequest:
    return InvalidKnowledgeRequest(details={"field": field_name, "reason": reason})


class DeterministicKnowledgeEngine:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._config = config or ChunkingConfig()

    def versions(self, source_type: SourceType) -> dict[str, int]:
        adapter = ADAPTERS[source_type]
        strategy, version = STRATEGIES[source_type]
        return {
            adapter: VERSIONS[adapter],
            redaction.RULE: redaction.VERSION,
            structure.RULE: structure.VERSION,
            strategy: version,
            IDENTIFIERS[0]: IDENTIFIERS[1],
            "chunk-max-chars": self._config.max_chars,
        }

    def _check(self, source: KnowledgeSource, content: IngestionInput) -> None:
        if content.type is not source.type:
            raise _refuse("content", "type_mismatch")
        if content.upload is not None and content.upload.path != source.path:
            raise _refuse("content.path", "another_document")  # a source is one document
        record = content.decision or content.requirement
        if record is not None:
            if source.record is None or record.id != source.record.record_id:
                raise _refuse("content.record", "another_record")
            if record.project_id != source.project_id:
                raise _refuse("content.record", "another_record")

    @staticmethod
    def _extract(content: IngestionInput) -> Extraction:
        if content.upload is not None:
            return extract_upload(content.upload)
        if content.decision is not None:
            return extract_decision(content.decision)
        if content.requirement is None:  # IngestionInput guarantees exactly one
            raise _refuse("content", "exactly_one_input")
        return extract_requirement(content.requirement)

    def ingest(
        self, source: KnowledgeSource, content: IngestionInput, run: IngestionRun, at: datetime
    ) -> IngestionOutcome:
        self._check(source, content)
        before = source.status
        working = source.begin_ingestion(at)  # refuses archived or busy sources
        run = run.start(at, self.versions(source.type))
        try:
            run = run.at_stage(Stage.EXTRACTING)
            extraction = self._extract(content)
            run = run.at_stage(Stage.NORMALIZING, extraction.checksum)
            if extraction.record is not None:
                working = working.with_record(extraction.record)  # the record version read
            if extraction.checksum == source.indexed_checksum:
                return IngestionOutcome(working.unchanged(at), run.unchanged(Counts(documents=1), at))
            number = (source.indexed_version or 0) + 1
            run = run.at_stage(Stage.CHUNKING)
            chunked = chunk(extraction, source.id, number, self._config)
            run = run.at_stage(Stage.INDEXING)
            counts = Counts(documents=1, chunks=len(chunked.chunks), skipped=chunked.skipped)
            version = SourceVersion(
                source.id, number, extraction.checksum, run.id, at, 1, len(chunked.chunks), chunked.versions,
                extraction.record,
            )  # fmt: skip
            return IngestionOutcome(
                working.indexed(number, extraction.checksum, at),
                run.complete(number, counts, at, chunked.warnings),
                Indexed(version, chunked.document, chunked.chunks),
            )
        except ExtractionFailed as failure:
            return IngestionOutcome(working.ingestion_failed(at, before), run.fail(failure.errors, at))

    def snapshot_changed(self, source: KnowledgeSource, content: IngestionInput) -> bool:
        if source.record is None or source.indexed_checksum is None:
            return False  # nothing indexed from a record: nothing to be out of date
        self._check(source, content)
        try:
            return self._extract(content).checksum != source.indexed_checksum
        except ExtractionFailed:
            return True  # the record can no longer be read as it was (e.g. deleted)
