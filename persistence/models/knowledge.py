"""Project knowledge records.

- ``knowledge_sources``: one uploaded document or one ArchitectOS record (an ADR, a requirement) of a
  project — its identity (never changed), where its index stands, the version in force and its
  lifecycle. Never deleted (archived instead). One active source per path, and per record.
- ``knowledge_ingestion_runs``: each attempt to read a source, written once when it ends.
- ``knowledge_source_versions``: each indexed reading of a source, with the run that made it.
- ``knowledge_documents``: the document of each version.
- ``knowledge_chunks``: the passages of each version, with their location, the identifiers they name
  and their terms (``knowledge-terms@1``) — GIN-indexed for the retrieval prefilter.

Versions, documents, passages and runs are append-only; every foreign key is same-project. No vector,
embedding or similarity score is stored.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.knowledge.values import (
    ContentType,
    IndexStatus,
    IngestionStatus,
    Lifecycle,
    SourceType,
    Stage,
    Trigger,
    Verification,
)

from ._checks import in_values
from .base import Base, CreatedAt

HASH = "'^[0-9a-f]{64}$'"
RECORDS = "('decision', 'requirement')"


class KnowledgeSourceRecord(Base):
    __tablename__ = "knowledge_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str | None] = mapped_column(Text)
    record_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    record_label: Mapped[str | None] = mapped_column(Text)
    record_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle: Mapped[str] = mapped_column(Text, nullable=False)
    indexed_version: Mapped[int | None] = mapped_column(Integer)
    indexed_checksum: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of same-project foreign keys
        CheckConstraint(in_values("type", SourceType), name="type"),
        CheckConstraint(in_values("status", IndexStatus), name="status"),
        CheckConstraint(in_values("lifecycle", Lifecycle), name="lifecycle"),
        CheckConstraint(f"(type IN {RECORDS}) = (record_id IS NOT NULL)", name="record_when_record"),
        CheckConstraint(f"(type IN {RECORDS}) = (path IS NULL)", name="path_when_upload"),
        CheckConstraint("(record_id IS NULL) = (record_label IS NULL)", name="record_label"),
        CheckConstraint("(type = 'requirement') = (record_version IS NOT NULL)", name="record_version"),
        CheckConstraint("(indexed_version IS NULL) = (indexed_checksum IS NULL)", name="indexed_complete"),
        CheckConstraint("indexed_version IS NULL OR indexed_version >= 1", name="indexed_version_positive"),
        CheckConstraint(f"indexed_checksum IS NULL OR indexed_checksum ~ {HASH}", name="checksum_format"),
        CheckConstraint(
            "status NOT IN ('indexed', 'stale') OR indexed_version IS NOT NULL", name="indexed_has_version"
        ),
        CheckConstraint("(lifecycle = 'archived') = (archived_at IS NOT NULL)", name="archived_at"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="name_length"),
        CheckConstraint("path IS NULL OR char_length(path) BETWEEN 1 AND 256", name="path_length"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata_object"),
        Index(
            "uq_knowledge_sources_active_path",
            "project_id",
            "path",
            unique=True,
            postgresql_where=text("lifecycle = 'active' AND path IS NOT NULL"),
        ),
        Index(
            "uq_knowledge_sources_active_record",
            "project_id",
            "record_id",
            unique=True,
            postgresql_where=text("lifecycle = 'active' AND record_id IS NOT NULL"),
        ),
        Index("ix_knowledge_sources_listing", "project_id", "lifecycle", "id"),
    )


class KnowledgeIngestionRunRecord(CreatedAt, Base):
    __tablename__ = "knowledge_ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str | None] = mapped_column(Text)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    indexed_version: Mapped[int | None] = mapped_column(Integer)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    errors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    retry_of: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("id", "project_id"),
        ForeignKeyConstraint(
            ["source_id", "project_id"],
            ["knowledge_sources.id", "knowledge_sources.project_id"],
            name="fk_knowledge_ingestion_runs_sources",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["retry_of", "project_id"],
            ["knowledge_ingestion_runs.id", "knowledge_ingestion_runs.project_id"],
            name="fk_knowledge_ingestion_runs_retry_of",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("trigger", Trigger), name="trigger"),
        CheckConstraint(in_values("status", IngestionStatus), name="status"),
        CheckConstraint(in_values("stage", Stage), name="stage"),
        CheckConstraint(
            "status IN ('completed', 'completed_with_warnings', 'unchanged', 'failed')", name="ended"
        ),  # written once, when the run has ended
        CheckConstraint(
            "(status IN ('completed', 'completed_with_warnings')) = (indexed_version IS NOT NULL)",
            name="indexed_when_completed",
        ),
        CheckConstraint("(status = 'failed') = (jsonb_array_length(errors) > 0)", name="errors_when_failed"),
        CheckConstraint(f"checksum IS NULL OR checksum ~ {HASH}", name="checksum_format"),
        CheckConstraint("jsonb_typeof(counts) = 'object'", name="counts_object"),
        CheckConstraint("jsonb_typeof(versions) = 'object'", name="versions_object"),
        CheckConstraint("jsonb_typeof(warnings) = 'array'", name="warnings_array"),
        CheckConstraint("jsonb_typeof(errors) = 'array'", name="errors_array"),
        Index("ix_knowledge_ingestion_runs_source", "project_id", "source_id", "id"),
    )


class KnowledgeSourceVersionRecord(Base):
    __tablename__ = "knowledge_source_versions"

    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    checksum: Mapped[str] = mapped_column(Text, nullable=False)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    documents: Mapped[int] = mapped_column(Integer, nullable=False)
    chunks: Mapped[int] = mapped_column(Integer, nullable=False)
    versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    record: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))

    __table_args__ = (
        PrimaryKeyConstraint("source_id", "number"),
        ForeignKeyConstraint(
            ["source_id", "project_id"],
            ["knowledge_sources.id", "knowledge_sources.project_id"],
            name="fk_knowledge_source_versions_sources",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["ingestion_run_id", "project_id"],
            ["knowledge_ingestion_runs.id", "knowledge_ingestion_runs.project_id"],
            name="fk_knowledge_source_versions_runs",
            ondelete="RESTRICT",
        ),
        CheckConstraint("number >= 1", name="number_positive"),
        CheckConstraint(f"checksum ~ {HASH}", name="checksum_format"),
        CheckConstraint("documents >= 1 AND chunks >= 0", name="counts"),
        CheckConstraint("jsonb_typeof(versions) = 'object'", name="versions_object"),
        Index("ix_knowledge_source_versions_project", "project_id"),
    )


class KnowledgeDocumentRecord(Base):
    __tablename__ = "knowledge_documents"

    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[str] = mapped_column(Text, nullable=False)
    record_status: Mapped[str | None] = mapped_column(Text)
    document_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("source_id", "source_version"),  # one document per version
        ForeignKeyConstraint(
            ["source_id", "source_version"],
            ["knowledge_source_versions.source_id", "knowledge_source_versions.number"],
            name="fk_knowledge_documents_versions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_id", "project_id"],
            ["knowledge_sources.id", "knowledge_sources.project_id"],
            name="fk_knowledge_documents_sources",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("content_type", ContentType), name="content_type"),
        CheckConstraint(in_values("verification", Verification), name="verification"),
        CheckConstraint(f"checksum ~ {HASH}", name="checksum_format"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata_object"),
    )


class KnowledgeChunkRecord(Base):
    __tablename__ = "knowledge_chunks"

    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    strategy: Mapped[str] = mapped_column(Text, nullable=False)
    occurrence: Mapped[int] = mapped_column(Integer, nullable=False)
    identifiers: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    terms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("source_id", "source_version", "chunk_id"),
        UniqueConstraint("source_id", "source_version", "sequence"),
        ForeignKeyConstraint(
            ["source_id", "source_version"],
            ["knowledge_source_versions.source_id", "knowledge_source_versions.number"],
            name="fk_knowledge_chunks_versions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_id", "project_id"],
            ["knowledge_sources.id", "knowledge_sources.project_id"],
            name="fk_knowledge_chunks_sources",
            ondelete="RESTRICT",
        ),
        CheckConstraint("char_length(text) BETWEEN 1 AND 4000", name="text_length"),
        CheckConstraint("sequence >= 0 AND occurrence >= 0", name="order"),
        CheckConstraint("jsonb_typeof(locator) = 'object'", name="locator_object"),
        Index("ix_knowledge_chunks_terms", "terms", postgresql_using="gin"),
        Index("ix_knowledge_chunks_identifiers", "identifiers", postgresql_using="gin"),
        Index("ix_knowledge_chunks_project", "project_id"),
    )
