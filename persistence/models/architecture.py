import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.architecture.versions import MAX_REASON_LENGTH, MAX_SUMMARY_LENGTH, RevisionSource

from .base import Base, CreatedAt, Timestamps, UuidPrimaryKey

MAX_IR_BYTES = 16 * 1024 * 1024
MAX_LAYOUT_BYTES = 1024 * 1024
_SOURCES = ", ".join(f"'{s.value}'" for s in RevisionSource)


class ArchitectureRecord(UuidPrimaryKey, Timestamps, Base):
    """A project's architecture (at most one per project). Its content is in its revisions; this
    row says which one is current. A deferred foreign key guarantees the current revision exists,
    and deletion is not possible while revisions reference it (history is never lost)."""

    __tablename__ = "architectures"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        UniqueConstraint("project_id"),  # one architecture per project; serves the RESTRICT check
        UniqueConstraint("id", "project_id"),  # target of the same-project foreign keys
        CheckConstraint("current_revision >= 1", name="current_revision_positive"),
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
    # No foreign key, like the other append-only tables: an immutable row cannot be SET NULL.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        UniqueConstraint("architecture_id", "number"),  # one revision n; target of the parent key
        UniqueConstraint("project_id", "number"),  # lookups by project (one architecture per project)
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
        CheckConstraint("number >= 1", name="number_positive"),
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
