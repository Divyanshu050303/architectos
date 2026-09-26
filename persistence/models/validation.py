"""Validation runs and their findings. Both tables are append-only (a run is stored once, completed
or failed; triggers refuse updates and deletes), belong to one project, and reference the
architecture revision they validated through same-project foreign keys."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.validation.results import Category, Severity
from core.domain.validation.runs import RunStatus

from ._checks import in_values
from .base import Base, CreatedAt, UuidPrimaryKey


class ValidationRunRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "validation_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    architecture_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    profile: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # No foreign key, like the other append-only tables: an immutable row cannot be SET NULL.
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rule_set: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {id, version, rules}
    context_fingerprint: Mapped[str | None] = mapped_column(Text)
    result_fingerprint: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    requirement_results: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    failures: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # config, policy, requirements
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of the findings' same-project foreign key
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_validation_runs_architecture_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_validation_runs_revision_architecture_revisions",
            ondelete="RESTRICT",
        ),
        # An architecture's runs, newest first (listing, optionally by revision).
        # Also serves the RESTRICT checks on architectures and their revisions.
        Index(None, "architecture_id", "requested_at", "id"),
        CheckConstraint(in_values("status", RunStatus), name="status"),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        CheckConstraint("revision_content_hash ~ '^[0-9a-f]{64}$'", name="revision_content_hash_format"),
        CheckConstraint("char_length(profile) BETWEEN 1 AND 64", name="profile_length"),
        CheckConstraint(
            "status <> 'completed' OR (completed_at IS NOT NULL AND result_fingerprint IS NOT NULL "
            "AND summary IS NOT NULL AND rule_set IS NOT NULL)",
            name="completed_has_result",
        ),
        CheckConstraint(
            "status <> 'failed' OR (completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="failed_has_error",
        ),
        CheckConstraint("jsonb_typeof(inputs) = 'object'", name="inputs_object"),
    )


class ValidationFindingRecord(UuidPrimaryKey, Base):
    """One finding of a run, at its position in the run's canonical order (most severe first).
    ``data`` is the whole finding; the other columns are copies for filtering."""

    __tablename__ = "validation_findings"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_id: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entity_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "position"),  # the canonical order; paging walks it
        UniqueConstraint("run_id", "finding_id"),
        ForeignKeyConstraint(
            ["run_id", "project_id"],
            ["validation_runs.id", "validation_runs.project_id"],
            name="fk_validation_findings_run_validation_runs",
            ondelete="RESTRICT",
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint(in_values("severity", Severity), name="severity"),
        CheckConstraint(in_values("category", Category), name="category"),
        CheckConstraint("jsonb_typeof(entity_ids) = 'array'", name="entity_ids_array"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )
