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
    Text,
    UniqueConstraint,
    Uuid,
    column,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.architecture.entities import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH
from core.domain.architecture.versions import MAX_REASON_LENGTH, MAX_SUMMARY_LENGTH, RevisionSource

from .base import Base, CreatedAt, Timestamps, UuidPrimaryKey

MAX_IR_BYTES = 16 * 1024 * 1024
MAX_LAYOUT_BYTES = 1024 * 1024
_SOURCES = ", ".join(f"'{s.value}'" for s in RevisionSource)


class ArchitectureRecord(UuidPrimaryKey, Timestamps, Base):
    """An architecture of a project: its metadata, lifecycle and current revision. Its content is
    in its revisions; a deferred foreign key guarantees the current revision exists. Deletion is
    soft (``deleted_at``, only from the archived state), so revisions are never lost."""

    __tablename__ = "architectures"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of the same-project foreign keys
        # A project's live architectures, newest first (listing); also serves the RESTRICT check.
        Index(
            "ix_architectures_project_id_created_at_id_live",
            "project_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Names are unique among a project's live architectures, ignoring case.
        Index(
            "uq_architectures_project_id_name_live",
            "project_id",
            func.lower(column("name")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("current_revision >= 1", name="current_revision_positive"),
        CheckConstraint(f"char_length(name) BETWEEN 1 AND {MAX_NAME_LENGTH}", name="name_length"),
        CheckConstraint(f"char_length(description) <= {MAX_DESCRIPTION_LENGTH}", name="description_length"),
        CheckConstraint("status IN ('active', 'archived')", name="status_valid"),
        CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL)", name="archived_at_matches_status"
        ),
        CheckConstraint("deleted_at IS NULL OR status = 'archived'", name="deleted_only_when_archived"),
        ForeignKeyConstraint(
            ["id", "current_revision"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_architectures_current_revision_architecture_revisions",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
    )


class ArchitectureRevisionRecord(UuidPrimaryKey, CreatedAt, Base):
    """One immutable revision: the validated Architecture IR in its canonical JSON form. Append-only
    (trigger). The parent is the previous revision of the same architecture; the architecture and
    the requirement set it was designed against belong to the same project."""

    __tablename__ = "architecture_revisions"

    architecture_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_number: Mapped[int | None] = mapped_column(Integer)
    ir: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ir_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    requirement_set_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    restored_from_number: Mapped[int | None] = mapped_column(Integer)  # the revision it restores
    # No foreign key, like the other append-only tables: an immutable row cannot be SET NULL.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        UniqueConstraint("architecture_id", "number"),  # revision n of an architecture; all lookups
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_architecture_revisions_architecture_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "parent_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_architecture_revisions_parent_architecture_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["requirement_set_id", "project_id"],
            ["requirement_sets.id", "requirement_sets.project_id"],
            name="fk_architecture_revisions_requirement_set_requirement_sets",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "restored_from_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_architecture_revisions_restored_from_architecture_revisions",
            ondelete="RESTRICT",
        ),
        CheckConstraint("number >= 1", name="number_positive"),
        CheckConstraint(
            "restored_from_number IS NULL OR restored_from_number < number",
            name="restores_an_earlier_revision",
        ),
        CheckConstraint(
            "(number = 1 AND parent_number IS NULL) OR parent_number = number - 1",
            name="parent_is_previous",
        ),
        CheckConstraint("ir_schema_version >= 1", name="ir_schema_version_positive"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        CheckConstraint(f"source IN ({_SOURCES})", name="source_valid"),
        CheckConstraint(f"char_length(summary) BETWEEN 1 AND {MAX_SUMMARY_LENGTH}", name="summary_length"),
        CheckConstraint(
            f"reason IS NULL OR char_length(reason) BETWEEN 1 AND {MAX_REASON_LENGTH}",
            name="reason_length",
        ),
        CheckConstraint("jsonb_typeof(ir) = 'object'", name="ir_object"),
        CheckConstraint(f"octet_length(ir::text) <= {MAX_IR_BYTES}", name="ir_size"),
    )


class ArchitectureLayoutRecord(Base):
    """Where each node is drawn: presentation only, never versioned, last write wins."""

    __tablename__ = "architecture_layouts"

    architecture_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    positions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_architecture_layouts_architecture_architectures",
            ondelete="RESTRICT",
        ),
        CheckConstraint("jsonb_typeof(positions) = 'object'", name="positions_object"),
        CheckConstraint(f"octet_length(positions::text) <= {MAX_LAYOUT_BYTES}", name="positions_size"),
    )
