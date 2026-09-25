import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementSource,
    RequirementStatus,
    RequirementType,
)

from ._checks import in_values
from .base import Base, CreatedAt, Timestamps, UuidPrimaryKey

CATEGORY_PATTERN = r"^[a-z][a-z0-9_]*$"
MAX_STRUCTURED_DATA_BYTES = 16_384


class RequirementContent:
    """The fields that make up one state of a requirement. Stored on the requirement (its current
    state, for listing and search) and on every version (the immutable history)."""

    type: Mapped[str] = mapped_column(Text, nullable=False)
    # Free-form but well-formed (e.g. "throughput", "rpo"): categories are an open vocabulary
    # validated by the domain, so a new one does not need a migration.
    category: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    # Confidence in the *interpretation* of the requirement (e.g. by an extractor), in [0, 1];
    # NULL when a person entered it directly. It says nothing about whether the requirement is true.
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    # Metric, operator, value, unit, percentile, ... Validated by the domain; data, never code.
    structured_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


def content_checks() -> tuple[CheckConstraint, ...]:
    """The same guarantees on the current state and on every version."""
    return (
        CheckConstraint(in_values("type", RequirementType), name="type"),
        CheckConstraint(
            f"char_length(category) BETWEEN 1 AND 64 AND category ~ '{CATEGORY_PATTERN}'",
            name="category_format",
        ),
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="title_length"),
        CheckConstraint("char_length(statement) BETWEEN 1 AND 5000", name="statement_length"),
        CheckConstraint(in_values("priority", RequirementPriority), name="priority"),
        CheckConstraint(in_values("status", RequirementStatus), name="status"),
        CheckConstraint(in_values("source", RequirementSource), name="source"),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"),
        # An interpretation by a machine must say how sure it is.
        CheckConstraint("source <> 'ai' OR confidence IS NOT NULL", name="ai_confidence"),
        CheckConstraint("jsonb_typeof(structured_data) = 'object'", name="structured_data_object"),
        CheckConstraint(
            f"octet_length(structured_data::text) <= {MAX_STRUCTURED_DATA_BYTES}",
            name="structured_data_size",
        ),
    )


class RequirementRecord(UuidPrimaryKey, Timestamps, RequirementContent, Base):
    """A requirement of a project, in its current state. Its history is in requirement_versions.

    Every change appends a version and moves ``current_version`` in the same transaction; the
    deferred foreign key below guarantees the current state always has its version row.
    Deletion is soft, so versions referenced by requirement sets are never lost.
    """

    __tablename__ = "requirements"

    # RESTRICT: projects are only soft-deleted; requirement history cannot vanish through a cascade.
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    # Human reference within the project ("REQ-12"); allocated under the project row lock, never reused.
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # SET NULL: a requirement outlives the account of whoever created it.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Also serves the RESTRICT check on projects(id).
        UniqueConstraint("project_id", "number"),
        # Target of the requirement set items' foreign key: an item's requirement must belong to
        # the set's project, enforced by the database.
        UniqueConstraint("id", "project_id"),
        # Listing a project's live requirements, newest first, keyset on (created_at, id).
        Index(
            "ix_requirements_project_id_created_at_id_live",
            "project_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # The current state must exist as a version. Deferred: the requirement and its first
        # version are inserted in the same transaction. use_alter breaks the table cycle.
        ForeignKeyConstraint(
            ["id", "current_version"],
            ["requirement_versions.requirement_id", "requirement_versions.version"],
            name="fk_requirements_current_version_requirement_versions",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint("number >= 1", name="number_positive"),
        CheckConstraint("current_version >= 1", name="current_version_positive"),
        *content_checks(),
    )


class RequirementVersionRecord(UuidPrimaryKey, CreatedAt, RequirementContent, Base):
    """One immutable state of a requirement. Append-only (enforced by a trigger): versions are what
    requirement sets, architectures and validation results refer to, so they must never change."""

    __tablename__ = "requirement_versions"

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("requirements.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Why this version exists ("Traffic forecast increased from 2K to 5K RPS").
    change_reason: Mapped[str | None] = mapped_column(Text)
    # No foreign key, like audit_logs.actor_user_id: an immutable row cannot be SET NULL, and the
    # history must survive the author's account.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        # Also serves the foreign key from requirements and the RESTRICT check on requirements(id).
        UniqueConstraint("requirement_id", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "change_reason IS NULL OR char_length(change_reason) BETWEEN 1 AND 500",
            name="change_reason_length",
        ),
        *content_checks(),
    )
