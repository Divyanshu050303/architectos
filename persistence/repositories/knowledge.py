"""Knowledge sources, runs, versions, documents and passages. Every read is scoped by project; a passage
read back is checked against its stored id (its content must still make that id); retrieval is a
prefilter by shared terms or named identifiers over the indexed versions of the project's active
sources — the engine decides what is returned."""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Integer, Text, Uuid, bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.knowledge.documents import KnowledgeChunk, Locator
from core.domain.knowledge.errors import InvalidKnowledgeRecord, KnowledgeSourceExists
from core.domain.knowledge.ingestion import Counts, IngestionError, IngestionRun
from core.domain.knowledge.ports import Indexed
from core.domain.knowledge.retrieval import Candidate, RetrievalQuery, Scope
from core.domain.knowledge.sources import KnowledgeSource, RecordRef
from core.domain.knowledge.values import (
    IndexStatus,
    IngestionStatus,
    Lifecycle,
    SourceType,
    Stage,
    Trigger,
    Verification,
)
from persistence.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    KnowledgeIngestionRunRecord,
    KnowledgeSourceRecord,
    KnowledgeSourceVersionRecord,
)
from persistence.repositories._errors import violated_constraint

S = KnowledgeSourceRecord
R = KnowledgeIngestionRunRecord
UNIQUE = {"uq_knowledge_sources_active_path", "uq_knowledge_sources_active_record"}
_FILTERS = (
    bindparam("project_id", type_=Uuid),
    bindparam("source_ids", type_=ARRAY(Uuid)),
    bindparam("types", type_=ARRAY(Text)),
)
# Passages of the version in force of the project's active sources within the query's filters that
# name a requested identifier or share a term — identifiers first, then most shared terms.
_CANDIDATES = text(
    """
    SELECT c.source_id, c.source_version, c.chunk_id, c.document_id, c.sequence, c.text, c.locator,
           c.strategy, c.occurrence, c.identifiers,
           s.name AS source_name, s.type AS source_type, s.status AS source_status,
           d.verification, d.record_status
    FROM knowledge_chunks c
    JOIN knowledge_sources s ON s.id = c.source_id AND s.indexed_version = c.source_version
    JOIN knowledge_documents d ON d.source_id = c.source_id AND d.source_version = c.source_version
    WHERE c.project_id = :project_id AND s.project_id = :project_id AND s.lifecycle = 'active'
      AND (cardinality(:source_ids) = 0 OR s.id = ANY(:source_ids))
      AND (cardinality(:types) = 0 OR s.type = ANY(:types))
      AND (:include_stale OR s.status <> 'stale')
      AND (c.identifiers && :identifiers OR c.terms && :terms)
    ORDER BY (c.identifiers && :identifiers) DESC,
             (SELECT count(*) FROM unnest(c.terms) AS t(term) WHERE t.term = ANY(:terms)) DESC,
             c.source_id, c.sequence
    LIMIT :limit
    """
).bindparams(*_FILTERS, bindparam("identifiers", type_=ARRAY(Text)), bindparam("terms", type_=ARRAY(Text)))
# What a search covers: the project's active sources within the same filters.
_SCOPE = text(
    """
    SELECT count(*) FILTER (WHERE s.indexed_version IS NOT NULL AND (:include_stale OR s.status <> 'stale'))
               AS searched,
           count(*) FILTER (WHERE s.indexed_version IS NULL) AS not_indexed,
           count(*) FILTER (WHERE NOT :include_stale AND s.status = 'stale') AS stale_excluded
    FROM knowledge_sources s
    WHERE s.project_id = :project_id AND s.lifecycle = 'active'
      AND (cardinality(:source_ids) = 0 OR s.id = ANY(:source_ids))
      AND (cardinality(:types) = 0 OR s.type = ANY(:types))
    """
).bindparams(*_FILTERS)
_CHUNK = text(
    """
    SELECT c.source_id, c.source_version, c.chunk_id, c.document_id, c.sequence, c.text, c.locator,
           c.strategy, c.occurrence, c.identifiers,
           s.name AS source_name, s.type AS source_type, s.status AS source_status,
           d.verification, d.record_status
    FROM knowledge_chunks c
    JOIN knowledge_sources s ON s.id = c.source_id
    JOIN knowledge_documents d ON d.source_id = c.source_id AND d.source_version = c.source_version
    WHERE c.project_id = :project_id AND s.project_id = :project_id AND s.lifecycle = 'active'
      AND c.source_id = :source_id AND c.chunk_id = :chunk_id
      AND c.source_version = coalesce(:version, s.indexed_version)
    """
).bindparams(
    bindparam("project_id", type_=Uuid),
    bindparam("source_id", type_=Uuid),
    bindparam("version", type_=Integer),
)


def _locator(data: Mapping[str, Any]) -> Locator:
    return Locator(
        tuple(data.get("heading_path") or ()), data.get("line_start"), data.get("line_end"),
        data.get("record"), data.get("field"),
    )  # fmt: skip


def to_source(r: KnowledgeSourceRecord) -> KnowledgeSource:
    kind = SourceType(r.type)
    record = None
    if r.record_id is not None and r.record_label is not None:
        record = RecordRef(kind, r.record_id, r.record_label, r.record_version)
    return KnowledgeSource(
        r.id, r.project_id, kind, r.name, r.created_by_user_id, r.created_at, r.updated_at, r.path, record,
        IndexStatus(r.status), Lifecycle(r.lifecycle), r.indexed_version, r.indexed_checksum,
        dict(r.source_metadata), r.archived_at, r.archived_by_user_id,
    )  # fmt: skip


def to_run(r: KnowledgeIngestionRunRecord) -> IngestionRun:
    errors = tuple(
        IngestionError(
            e["code"], e["message"], Stage(e["stage"]), _locator(e["locator"]) if e.get("locator") else None
        )
        for e in r.errors
    )
    return IngestionRun(
        r.id, r.project_id, r.source_id, Trigger(r.trigger), r.requested_by_user_id, r.requested_at,
        IngestionStatus(r.status), Stage(r.stage), r.checksum, Counts(**r.counts), dict(r.versions),
        r.indexed_version, tuple(r.warnings), errors, r.retry_of, r.started_at, r.completed_at,
    )  # fmt: skip


def _candidate(project_id: uuid.UUID, row: Any) -> Candidate:
    chunk = KnowledgeChunk(
        row.source_id, row.source_version, row.document_id, row.sequence, row.text, _locator(row.locator),
        row.strategy, row.occurrence, tuple(row.identifiers),
    )  # fmt: skip
    if chunk.id != row.chunk_id:  # the content no longer makes its id: a corrupted record
        raise InvalidKnowledgeRecord(details={"fields": ["chunk_id"]})
    return Candidate(
        project_id, chunk, row.source_name, SourceType(row.source_type), row.source_status == "stale",
        Verification(row.verification), row.record_status,
    )  # fmt: skip


def _filters(project_id: uuid.UUID, query: RetrievalQuery) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "source_ids": list(query.source_ids),
        "types": [t.value for t in query.source_types],
        "include_stale": query.include_stale,
    }


class SqlAlchemyKnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_source(self, source: KnowledgeSource) -> KnowledgeSource:
        record = source.record
        self._session.add(
            KnowledgeSourceRecord(
                id=source.id, project_id=source.project_id, type=source.type.value, name=source.name,
                path=source.path, record_id=record.record_id if record else None,
                record_label=record.label if record else None,
                record_version=record.version if record else None,
                status=source.status.value, lifecycle=source.lifecycle.value,
                indexed_version=source.indexed_version, indexed_checksum=source.indexed_checksum,
                source_metadata=dict(source.metadata), created_by_user_id=source.created_by_user_id,
                created_at=source.created_at, updated_at=source.updated_at,
            )
        )  # fmt: skip
        try:
            await self._session.flush()
        except IntegrityError as error:
            if violated_constraint(error) in UNIQUE:  # registered concurrently
                raise KnowledgeSourceExists(details={}) from None
            raise
        return source

    async def save_source(self, source: KnowledgeSource) -> KnowledgeSource:
        record = await self._session.scalar(
            select(S).where(S.id == source.id, S.project_id == source.project_id).with_for_update()
        )
        if record is None:
            raise LookupError(source.id)  # the service read it under the same lock: never expected
        record.status = source.status.value
        record.lifecycle = source.lifecycle.value
        record.indexed_version = source.indexed_version
        record.indexed_checksum = source.indexed_checksum
        record.record_version = source.record.version if source.record else None
        record.updated_at = source.updated_at
        record.archived_at = source.archived_at
        record.archived_by_user_id = source.archived_by_user_id
        await self._session.flush()
        return source

    async def get_source(
        self, project_id: uuid.UUID, source_id: uuid.UUID, *, for_update: bool = False
    ) -> KnowledgeSource | None:
        statement = select(S).where(S.project_id == project_id, S.id == source_id)
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return to_source(record) if record else None

    async def find_source(
        self, project_id: uuid.UUID, *, path: str | None = None, record_id: uuid.UUID | None = None
    ) -> KnowledgeSource | None:
        statement = select(S).where(S.project_id == project_id, S.lifecycle == Lifecycle.ACTIVE.value)
        if path is not None:
            statement = statement.where(S.path == path)
        elif record_id is not None:
            statement = statement.where(S.record_id == record_id)
        else:
            return None
        record = await self._session.scalar(statement)
        return to_source(record) if record else None

    async def list_sources(
        self,
        project_id: uuid.UUID,
        *,
        status: IndexStatus | None = None,
        source_type: SourceType | None = None,
        lifecycle: Lifecycle | None = Lifecycle.ACTIVE,
        after: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[KnowledgeSource]:
        statement = select(S).where(S.project_id == project_id)
        if status is not None:
            statement = statement.where(S.status == status.value)
        if source_type is not None:
            statement = statement.where(S.type == source_type.value)
        if lifecycle is not None:
            statement = statement.where(S.lifecycle == lifecycle.value)
        if after is not None:
            statement = statement.where(S.id > after)
        records = await self._session.scalars(statement.order_by(S.id).limit(limit))
        return [to_source(r) for r in records]

    async def add_run(self, run: IngestionRun) -> IngestionRun:
        self._session.add(
            KnowledgeIngestionRunRecord(
                id=run.id, project_id=run.project_id, source_id=run.source_id, trigger=run.trigger.value,
                status=run.status.value, stage=run.stage.value, checksum=run.checksum,
                counts=run.counts.to_dict(), versions=dict(run.versions), indexed_version=run.indexed_version,
                warnings=list(run.warnings), errors=[e.to_dict() for e in run.errors], retry_of=run.retry_of,
                requested_by_user_id=run.requested_by_user_id, requested_at=run.requested_at,
                started_at=run.started_at, completed_at=run.completed_at,
            )
        )  # fmt: skip
        await self._session.flush()
        return run

    async def get_run(
        self, project_id: uuid.UUID, source_id: uuid.UUID, run_id: uuid.UUID
    ) -> IngestionRun | None:
        record = await self._session.scalar(
            select(R).where(R.project_id == project_id, R.source_id == source_id, R.id == run_id)
        )
        return to_run(record) if record else None

    async def list_runs(
        self, project_id: uuid.UUID, source_id: uuid.UUID, *, after: uuid.UUID | None = None, limit: int = 50
    ) -> list[IngestionRun]:
        statement = select(R).where(R.project_id == project_id, R.source_id == source_id)
        if after is not None:
            statement = statement.where(R.id < after)
        records = await self._session.scalars(statement.order_by(R.id.desc()).limit(limit))
        return [to_run(r) for r in records]

    async def add_version(
        self, project_id: uuid.UUID, indexed: Indexed, terms: Mapping[str, tuple[str, ...]]
    ) -> None:
        version, document = indexed.version, indexed.document
        self._session.add(
            KnowledgeSourceVersionRecord(
                source_id=version.source_id, number=version.number, project_id=project_id,
                checksum=version.checksum, ingestion_run_id=version.ingestion_run_id,
                created_at=version.created_at,
                documents=version.documents, chunks=version.chunks, versions=dict(version.versions),
                record=version.record.to_dict() if version.record else None,
            )
        )  # fmt: skip
        await self._session.flush()
        self._session.add(
            KnowledgeDocumentRecord(
                source_id=document.source_id, source_version=document.source_version, project_id=project_id,
                document_id=document.id, content_type=document.content_type.value, checksum=document.checksum,
                reference=document.reference, title=document.title, verification=document.verification.value,
                record_status=document.record_status, document_metadata=dict(document.metadata),
            )
        )  # fmt: skip
        self._session.add_all(
            KnowledgeChunkRecord(
                source_id=c.source_id, source_version=c.source_version, chunk_id=c.id, project_id=project_id,
                document_id=c.document_id, sequence=c.sequence, text=c.text, locator=c.locator.to_dict(),
                strategy=c.strategy, occurrence=c.occurrence, identifiers=list(c.identifiers),
                terms=list(terms.get(c.id, ())),
            )
            for c in indexed.chunks
        )  # fmt: skip
        await self._session.flush()

    async def scope(self, project_id: uuid.UUID, query: RetrievalQuery) -> Scope:
        row = (await self._session.execute(_SCOPE, _filters(project_id, query))).one()
        return Scope(project_id, row.searched, row.not_indexed, row.stale_excluded)

    async def candidates(
        self, project_id: uuid.UUID, query: RetrievalQuery, terms: tuple[str, ...], limit: int
    ) -> list[Candidate]:
        if not terms and not query.identifiers:
            return []
        values = _filters(project_id, query) | {
            "identifiers": list(query.identifiers),
            "terms": list(terms),
            "limit": limit,
        }
        rows = await self._session.execute(_CANDIDATES, values)
        return [_candidate(project_id, row) for row in rows]

    async def chunk(
        self, project_id: uuid.UUID, source_id: uuid.UUID, chunk_id: str, version: int | None = None
    ) -> Candidate | None:
        values = {"project_id": project_id, "source_id": source_id, "chunk_id": chunk_id, "version": version}
        row = (await self._session.execute(_CHUNK, values)).first()
        return _candidate(project_id, row) if row else None
