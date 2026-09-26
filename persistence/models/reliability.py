"""Reliability analyses, their components and findings. All three tables are append-only (an
analysis is stored once, finished; triggers refuse updates and deletes), belong to one project, and
reference the architecture revision they analyzed through same-project foreign keys."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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

from core.domain.capacity.results import Certainty, ComponentStatus
from core.domain.reliability.results import FindingType, ReliabilityStatus
from core.domain.validation.results import Severity

from ._checks import in_values
from .base import Base, CreatedAt, UuidPrimaryKey

_STATUSES = ", ".join(f"'{s}'" for s in ("pending", "running", *(s.value for s in ReliabilityStatus)))


class ReliabilityAnalysisRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "reliability_analyses"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    architecture_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    # No foreign key, like the other append-only tables: an immutable row cannot be SET NULL.
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # request, requirements read
    model_set: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    context_fingerprint: Mapped[str | None] = mapped_column(Text)
    result_fingerprint: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    paths: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    objectives: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    unsupported: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of the rows' same-project foreign keys
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_reliability_analyses_architecture_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_reliability_analyses_revision_architecture_revisions",
            ondelete="RESTRICT",
        ),
        Index(None, "architecture_id", "requested_at", "id"),
        CheckConstraint(f"status IN ({_STATUSES})", name="status"),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        CheckConstraint("revision_content_hash ~ '^[0-9a-f]{64}$'", name="revision_content_hash_format"),
        CheckConstraint("label IS NULL OR char_length(label) BETWEEN 1 AND 100", name="label_length"),
        CheckConstraint(
            "status IN ('pending', 'running', 'failed') OR (completed_at IS NOT NULL "
            "AND result_fingerprint IS NOT NULL AND summary IS NOT NULL AND model_set IS NOT NULL)",
            name="finished_has_result",
        ),
        CheckConstraint(
            "status <> 'failed' OR (completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="failed_has_error",
        ),
        CheckConstraint("jsonb_typeof(inputs) = 'object'", name="inputs_object"),
        CheckConstraint("jsonb_typeof(paths) = 'array'", name="paths_array"),
        CheckConstraint("jsonb_typeof(objectives) = 'array'", name="objectives_array"),
    )


class ReliabilityComponentRecord(UuidPrimaryKey, Base):
    """One component's result in an analysis; ``data`` is the whole result."""

    __tablename__ = "reliability_components"

    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "node_id"),
        ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["reliability_analyses.id", "reliability_analyses.project_id"],
            name="fk_reliability_components_analysis_reliability_analyses",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("status", ComponentStatus), name="status"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )


class ReliabilityFindingRecord(UuidPrimaryKey, Base):
    """One finding of an analysis, at its position in the canonical order."""

    __tablename__ = "reliability_findings"

    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_id: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    certainty: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "position"),
        UniqueConstraint("analysis_id", "finding_id"),
        ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["reliability_analyses.id", "reliability_analyses.project_id"],
            name="fk_reliability_findings_analysis_reliability_analyses",
            ondelete="RESTRICT",
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("finding_id ~ '^rel_[0-9a-f]{16}$'", name="finding_id_format"),
        CheckConstraint(in_values("type", FindingType), name="type"),
        CheckConstraint(in_values("severity", Severity), name="severity"),
        CheckConstraint(in_values("certainty", Certainty), name="certainty"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )
