"""Capacity analyses, their components and bottlenecks. All three tables are append-only (an
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

from core.domain.capacity.results import AnalysisStatus, BottleneckCondition, Certainty, ComponentStatus

from ._checks import in_values
from .base import Base, CreatedAt, UuidPrimaryKey

_STATUSES = ", ".join(f"'{s}'" for s in ("pending", "running", *(s.value for s in AnalysisStatus)))


class CapacityAnalysisRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "capacity_analyses"

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
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # workload snapshot, settings
    model_set: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    context_fingerprint: Mapped[str | None] = mapped_column(Text)
    result_fingerprint: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    connections: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)  # demand per connection
    unsupported: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    scaling: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    unsupported_scaling: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    scenarios: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of the rows' same-project foreign keys
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_capacity_analyses_architecture_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_capacity_analyses_revision_architecture_revisions",
            ondelete="RESTRICT",
        ),
        # An architecture's analyses, newest first; also serves the RESTRICT checks.
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
        CheckConstraint("jsonb_typeof(scenarios) = 'array'", name="scenarios_array"),
    )


class CapacityComponentRecord(UuidPrimaryKey, Base):
    """One component's result in an analysis; ``data`` is the whole result."""

    __tablename__ = "capacity_components"

    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "node_id"),  # paging walks it
        ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["capacity_analyses.id", "capacity_analyses.project_id"],
            name="fk_capacity_components_analysis_capacity_analyses",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("status", ComponentStatus), name="status"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )


class CapacityBottleneckRecord(UuidPrimaryKey, Base):
    """One bottleneck candidate of an analysis, at its position in the canonical order."""

    __tablename__ = "capacity_bottlenecks"

    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    certainty: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "position"),
        ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["capacity_analyses.id", "capacity_analyses.project_id"],
            name="fk_capacity_bottlenecks_analysis_capacity_analyses",
            ondelete="RESTRICT",
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint(in_values("condition", BottleneckCondition), name="condition"),
        CheckConstraint(in_values("certainty", Certainty), name="certainty"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )
