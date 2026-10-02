"""Knowledge use cases: register a document or an ArchitectOS record as a source of a project and index
it, re-index it, archive it, read sources, ingestion runs and passages — and retrieve evidence for a
query. This is the ``KnowledgeRetriever`` every consumer uses: authorized, project-scoped, cited.

Ingestion is synchronous and bounded, in one transaction under the source's row lock: the engine
reads the input (on a worker thread), then the run, the new version (document and passages, in
full) and the source are stored together — or, if the content cannot be indexed, only the failed run
and the source as it was. A record source reads the record as it is now; a requirement deleted since
is a failed ingestion (``record_deleted``), never a silent removal.

Freshness: a record source whose record now reads differently is marked ``stale`` when it is read
(``get``) or when one of its passages is a retrieval candidate — under the source's lock, so a
concurrent re-index is never overwritten. Nothing is re-indexed behind a person's back.

Access: registering, re-indexing and archiving need ``knowledge.manage``; reading and retrieval need
``knowledge.read``. Every lookup is scoped by project: a source, run or passage of another project or
tenant is not found. Audit entries carry identifiers, statuses and counts — never content, names or
paths. Metrics carry statuses and types only — never a query or a passage.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime

from core.domain import pagination
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.decisions.entities import Decision
from core.domain.decisions.errors import DecisionNotFound
from core.domain.metrics import Metrics, NullMetrics
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.requirements.entities import Requirement
from core.domain.requirements.errors import RequirementNotFound
from core.domain.unit_of_work import UnitOfWork

from .errors import (
    IngestionRunNotFound,
    InvalidKnowledgeRequest,
    KnowledgeChunkNotFound,
    KnowledgeSourceExists,
    KnowledgeSourceNotFound,
)
from .ingestion import IngestionError, IngestionRun
from .ports import IngestionInput, IngestionOutcome, KnowledgeEngine
from .retrieval import Candidate, RetrievalQuery, RetrievalResult
from .sources import KnowledgeSource, RecordRef
from .uploads import DocumentUpload
from .values import RECORDS, UPLOADED, IndexStatus, IngestionStatus, Lifecycle, SourceType, Stage, Trigger

MAX_PAGE = 100
MAX_CANDIDATES = 500
_SOURCES = "knowledge_sources"
_RUNS = "knowledge_ingestions"


def _invalid(field_name: str, reason: str) -> InvalidKnowledgeRequest:
    return InvalidKnowledgeRequest(details={"field": field_name, "reason": reason})


def encode_cursor(kind: str, last: uuid.UUID) -> str:
    return pagination.encode_cursor([kind, str(last)])


def decode_cursor(kind: str, raw: str) -> uuid.UUID:
    sort, last = pagination.decode_cursor(raw, length=2)
    if sort != kind:
        raise pagination.InvalidCursor
    try:
        return uuid.UUID(last)
    except ValueError:
        raise pagination.InvalidCursor from None


@dataclass(frozen=True, slots=True)
class SourceRegistration:
    """An uploaded document, or an ArchitectOS record (a decision or a requirement) by id."""

    upload: DocumentUpload | None = None
    record_type: SourceType | None = None
    record_id: uuid.UUID | None = None
    name: str | None = None  # default: the document's path, or the record's label

    def __post_init__(self) -> None:
        if (self.upload is None) == (self.record_id is None):
            raise _invalid("source", "upload_or_record")
        if self.record_id is not None and self.record_type not in RECORDS:
            raise _invalid("record.type", "not_a_record_type")
        if self.upload is not None and self.record_type is not None:
            raise _invalid("record.type", "uploads_have_no_record")


class KnowledgeService:
    def __init__(
        self,
        uow: UnitOfWork,
        engine: KnowledgeEngine,
        *,
        clock: Clock = utc_now,
        metrics: Metrics | None = None,
    ) -> None:
        self._uow = uow
        self._engine = engine
        self._clock = clock
        self._metrics = metrics or NullMetrics()

    # --- records -----------------------------------------------------------------------------------

    @staticmethod
    async def _record(
        uow: UnitOfWork, project_id: uuid.UUID, kind: SourceType, record_id: uuid.UUID
    ) -> Decision | Requirement | None:
        if kind is SourceType.DECISION:
            return await uow.decisions.get(project_id, record_id)
        return await uow.requirements.get(project_id, record_id)  # None once deleted

    @staticmethod
    def _input(record: Decision | Requirement) -> IngestionInput:
        if isinstance(record, Decision):
            return IngestionInput(decision=record)
        return IngestionInput(requirement=record)

    # --- ingestion ---------------------------------------------------------------------------------

    async def register(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, registration: SourceRegistration
    ) -> IngestionOutcome:
        """A new source of the project, indexed now (or failed, stored with why)."""
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.KNOWLEDGE_MANAGE, lock=ProjectLock.SHARE
            )
            now = self._clock()
            upload = registration.upload
            if upload is not None:
                existing = await uow.knowledge.find_source(project_id, path=upload.path)
                content = IngestionInput(upload=upload)
                name = registration.name or upload.path
                source = KnowledgeSource(
                    uuid.uuid7(), project_id, upload.source_type, name, user_id, now, now, path=upload.path
                )
            else:
                kind, record_id = registration.record_type, registration.record_id
                if kind is None or record_id is None:  # guaranteed by SourceRegistration
                    raise _invalid("record", "required")
                record = await self._record(uow, project_id, kind, record_id)
                if record is None:
                    raise DecisionNotFound if kind is SourceType.DECISION else RequirementNotFound
                existing = await uow.knowledge.find_source(project_id, record_id=record_id)
                content = self._input(record)
                version = record.version if isinstance(record, Requirement) else None
                ref = RecordRef(kind, record_id, record.reference, version)
                source = KnowledgeSource(
                    uuid.uuid7(),
                    project_id,
                    kind,
                    registration.name or ref.label,
                    user_id,
                    now,
                    now,
                    record=ref,
                )
            if existing is not None:
                raise KnowledgeSourceExists(details={"source_id": str(existing.id)})
            await uow.knowledge.add_source(source)
            await self._audit(uow, access, user_id, AuditAction.KNOWLEDGE_SOURCE_REGISTERED, source, {})
            outcome = await self._ingest(uow, access, user_id, source, content, Trigger.REGISTER, None, now)
        return outcome

    async def reindex(
        self,
        *,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str | None = None,
        retry_of: uuid.UUID | None = None,
    ) -> IngestionOutcome:
        """Read the source again: an upload's new content, or the record as it is now."""
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.KNOWLEDGE_MANAGE, lock=ProjectLock.SHARE
            )
            source = await self._source(uow, project_id, source_id, for_update=True)
            if retry_of is not None:
                previous = await uow.knowledge.get_run(project_id, source.id, retry_of)
                if previous is None:
                    raise IngestionRunNotFound
                if previous.status is not IngestionStatus.FAILED:
                    raise _invalid("retry_of", "not_a_failed_run")
            now = self._clock()
            if source.type in UPLOADED:
                if content is None or source.path is None:
                    raise _invalid("content", "required")
                given = IngestionInput(upload=DocumentUpload(source.path, content, source.type))
            else:
                if content is not None:
                    raise _invalid("content", "records_are_read_from_architectos")
                if source.record is None:  # guaranteed by KnowledgeSource
                    raise _invalid("record", "required")
                record = await self._record(uow, project_id, source.type, source.record.record_id)
                if record is None:
                    return await self._record_gone(uow, access, user_id, source, retry_of, now)
                given = self._input(record)
            return await self._ingest(uow, access, user_id, source, given, Trigger.REINDEX, retry_of, now)

    async def _ingest(
        self,
        uow: UnitOfWork,
        access: ProjectAccess,
        user_id: uuid.UUID,
        source: KnowledgeSource,
        content: IngestionInput,
        trigger: Trigger,
        retry_of: uuid.UUID | None,
        at: datetime,
    ) -> IngestionOutcome:
        run = IngestionRun(
            uuid.uuid7(), source.project_id, source.id, trigger, user_id, at, retry_of=retry_of
        )
        started = time.monotonic()
        outcome = await asyncio.to_thread(self._engine.ingest, source, content, run, at)
        await self._store(uow, access, user_id, outcome)
        status, kind = outcome.run.status.value, source.type.value
        self._metrics.increment("knowledge.ingestions", status=status, source_type=kind)
        self._metrics.observe(
            "knowledge.ingestion_seconds", time.monotonic() - started, status=status, source_type=kind
        )
        return outcome

    async def _record_gone(
        self,
        uow: UnitOfWork,
        access: ProjectAccess,
        user_id: uuid.UUID,
        source: KnowledgeSource,
        retry_of: uuid.UUID | None,
        at: datetime,
    ) -> IngestionOutcome:
        """The record a source snapshots no longer exists: a failed ingestion; the last version stays."""
        run = IngestionRun(
            uuid.uuid7(), source.project_id, source.id, Trigger.REINDEX, user_id, at, retry_of=retry_of
        )
        error = IngestionError("record_deleted", "The record no longer exists.", Stage.EXTRACTING)
        failed = (
            run.start(at, self._engine.versions(source.type)).at_stage(Stage.EXTRACTING).fail((error,), at)
        )
        outcome = IngestionOutcome(source.begin_ingestion(at).ingestion_failed(at, source.status), failed)
        await self._store(uow, access, user_id, outcome)
        self._metrics.increment("knowledge.ingestions", status="failed", source_type=source.type.value)
        return outcome

    async def _store(
        self, uow: UnitOfWork, access: ProjectAccess, user_id: uuid.UUID, outcome: IngestionOutcome
    ) -> None:
        """The run, the version it indexed (in full) and the source — in one transaction."""
        await uow.knowledge.add_run(outcome.run)  # first: the version it indexed refers to it
        if outcome.indexed is not None:
            terms = {c.id: self._engine.terms(c.text) for c in outcome.indexed.chunks}
            await uow.knowledge.add_version(outcome.source.project_id, outcome.indexed, terms)
        await uow.knowledge.save_source(outcome.source)  # last: only now does it point at the version
        run = outcome.run
        facts: dict[str, object] = {
            "run_id": str(run.id),
            "trigger": run.trigger.value,
            "status": run.status.value,
            "indexed_version": run.indexed_version,
            "chunks": run.counts.chunks,
            "errors": [e.code for e in run.errors],
        }
        await self._audit(uow, access, user_id, AuditAction.KNOWLEDGE_SOURCE_INGESTED, outcome.source, facts)

    async def archive(
        self, *, project_id: uuid.UUID, source_id: uuid.UUID, user_id: uuid.UUID
    ) -> KnowledgeSource:
        """No longer searched; its versions, passages and runs are kept."""
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.KNOWLEDGE_MANAGE, lock=ProjectLock.SHARE
            )
            source = await self._source(uow, project_id, source_id, for_update=True)
            archived = await uow.knowledge.save_source(source.archive(user_id, self._clock()))
            await self._audit(uow, access, user_id, AuditAction.KNOWLEDGE_SOURCE_ARCHIVED, archived, {})
        return archived

    # --- reading -----------------------------------------------------------------------------------

    @staticmethod
    async def _source(
        uow: UnitOfWork, project_id: uuid.UUID, source_id: uuid.UUID, *, for_update: bool = False
    ) -> KnowledgeSource:
        found = await uow.knowledge.get_source(project_id, source_id, for_update=for_update)
        if found is None:
            raise KnowledgeSourceNotFound
        return found

    async def _refresh(self, uow: UnitOfWork, project_id: uuid.UUID, source_id: uuid.UUID) -> KnowledgeSource:
        """A record source marked stale if its record now reads differently — under the source's lock."""
        source = await self._source(uow, project_id, source_id, for_update=True)
        current = source.status is IndexStatus.INDEXED and source.lifecycle is Lifecycle.ACTIVE
        if source.record is None or not current:
            return source
        record = await self._record(uow, project_id, source.type, source.record.record_id)
        if record is None or self._engine.snapshot_changed(source, self._input(record)):
            return await uow.knowledge.save_source(source.stale(self._clock()))
        return source

    async def get(
        self, *, project_id: uuid.UUID, source_id: uuid.UUID, user_id: uuid.UUID
    ) -> KnowledgeSource:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.KNOWLEDGE_READ)
            await self._source(uow, project_id, source_id)
            return await self._refresh(uow, project_id, source_id)

    async def list_sources(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        status: IndexStatus | None = None,
        source_type: SourceType | None = None,
        lifecycle: Lifecycle | None = Lifecycle.ACTIVE,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[KnowledgeSource]:
        size = max(1, min(limit, MAX_PAGE))
        after = decode_cursor(_SOURCES, cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.KNOWLEDGE_READ)
            rows = await uow.knowledge.list_sources(
                project_id,
                status=status,
                source_type=source_type,
                lifecycle=lifecycle,
                after=after,
                limit=size + 1,
            )
        page, more = rows[:size], len(rows) > size
        return pagination.Page(page, encode_cursor(_SOURCES, page[-1].id) if more and page else None)

    async def runs(
        self,
        *,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        user_id: uuid.UUID,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[IngestionRun]:
        size = max(1, min(limit, MAX_PAGE))
        after = decode_cursor(_RUNS, cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.KNOWLEDGE_READ)
            await self._source(uow, project_id, source_id)
            rows = await uow.knowledge.list_runs(project_id, source_id, after=after, limit=size + 1)
        page, more = rows[:size], len(rows) > size
        return pagination.Page(page, encode_cursor(_RUNS, page[-1].id) if more and page else None)

    async def run(
        self, *, project_id: uuid.UUID, source_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID
    ) -> IngestionRun:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.KNOWLEDGE_READ)
            found = await uow.knowledge.get_run(project_id, source_id, run_id)
        if found is None:
            raise IngestionRunNotFound
        return found

    async def passage(
        self,
        *,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        chunk_id: str,
        user_id: uuid.UUID,
        version: int | None = None,
    ) -> Candidate:
        """A passage by its id — of the version in force, or of an earlier ``version`` (for audit)."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.KNOWLEDGE_READ)
            source = await self._source(uow, project_id, source_id)
            if source.lifecycle is Lifecycle.ARCHIVED:
                raise KnowledgeChunkNotFound  # archived sources are never retrieved
            found = await uow.knowledge.chunk(project_id, source.id, chunk_id, version)
        if found is None:
            raise KnowledgeChunkNotFound
        return found

    # --- retrieval: the KnowledgeRetriever ---------------------------------------------------------

    async def retrieve(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, query: RetrievalQuery
    ) -> RetrievalResult:
        started = time.monotonic()
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.KNOWLEDGE_READ)
            terms = self._engine.terms(query.text) if query.text else ()
            candidates = await uow.knowledge.candidates(project_id, query, terms, MAX_CANDIDATES)
            records = sorted(
                {c.chunk.source_id for c in candidates if c.source_type in RECORDS and not c.stale}
            )
            refreshed = [await self._refresh(uow, project_id, source_id) for source_id in records]
            stale = {s.id for s in refreshed if s.status is IndexStatus.STALE}
            if stale:
                candidates = [replace(c, stale=True) if c.chunk.source_id in stale else c for c in candidates]
            scope = await uow.knowledge.scope(project_id, query)
        result = self._engine.retrieve(query, candidates, scope)
        outcome = "insufficient_evidence" if result.insufficient_evidence else "found"
        self._metrics.increment("knowledge.retrievals", outcome=outcome)
        self._metrics.observe("knowledge.retrieval_seconds", time.monotonic() - started, outcome=outcome)
        return result

    # --- audit -------------------------------------------------------------------------------------

    @staticmethod
    async def _audit(
        uow: UnitOfWork,
        access: ProjectAccess,
        user_id: uuid.UUID,
        action: AuditAction,
        source: KnowledgeSource,
        facts: dict[str, object],
    ) -> None:
        metadata: dict[str, object] = {
            "project_id": str(source.project_id),
            "source_type": source.type.value,
            "index_status": source.status.value,
            "lifecycle": source.lifecycle.value,
        }
        await uow.audit.record(
            AuditEvent(
                action,
                actor_user_id=user_id,
                organization_id=access.project.organization_id,
                resource_type="knowledge_source",
                resource_id=source.id,
                metadata=metadata | facts,
            )
        )
