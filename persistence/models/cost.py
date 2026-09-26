"""Cost analyses and their line items. Both tables are append-only (an analysis is stored once,
finished; triggers refuse updates and deletes), belong to one project, and reference the revision
they priced, the pricing snapshot they priced with (same organization) and the capacity analysis
they took usage from (same project) through foreign keys."""

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

from core.domain.cost.results import CostCategory, CostKind, CostStatus, LineStatus

from ._checks import in_values
from .base import Base, CreatedAt, UuidPrimaryKey

_STATUSES = ", ".join(f"'{s}'" for s in ("pending", "running", *(s.value for s in CostStatus)))


class CostAnalysisRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "cost_analyses"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # the snapshot's
    architecture_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    capacity_analysis_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    # No foreign key, like the other append-only tables: an immutable row cannot be SET NULL.
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # request, provider, scenarios
    snapshot_hash: Mapped[str | None] = mapped_column(Text)
    model_set: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    context_fingerprint: Mapped[str | None] = mapped_column(Text)
    result_fingerprint: Mapped[str | None] = mapped_column(Text)
    totals: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # breakdowns, unknown items, drivers
    unsupported: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    assumptions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    scenarios: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)  # projections and comparisons
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of the line items' same-project foreign key
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_cost_analyses_architecture_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_cost_analyses_revision_architecture_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["pricing_snapshots.id", "pricing_snapshots.organization_id"],
            name="fk_cost_analyses_snapshot_pricing_snapshots",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["capacity_analysis_id", "project_id"],
            ["capacity_analyses.id", "capacity_analyses.project_id"],
            name="fk_cost_analyses_capacity_capacity_analyses",
            ondelete="RESTRICT",
        ),
        # An architecture's analyses, newest first; also serves the RESTRICT checks.
        Index(None, "architecture_id", "requested_at", "id"),
        Index(None, "snapshot_id"),
        Index(None, "capacity_analysis_id"),
        CheckConstraint(f"status IN ({_STATUSES})", name="status"),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        CheckConstraint("revision_content_hash ~ '^[0-9a-f]{64}$'", name="revision_content_hash_format"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint("label IS NULL OR char_length(label) BETWEEN 1 AND 100", name="label_length"),
        CheckConstraint(
            "status IN ('pending', 'running', 'failed') OR (completed_at IS NOT NULL "
            "AND result_fingerprint IS NOT NULL AND totals IS NOT NULL AND summary IS NOT NULL "
            "AND model_set IS NOT NULL)",
            name="finished_has_result",
        ),
        CheckConstraint(
            "status <> 'failed' OR (completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="failed_has_error",
        ),
        CheckConstraint("jsonb_typeof(inputs) = 'object'", name="inputs_object"),
        CheckConstraint("jsonb_typeof(scenarios) = 'array'", name="scenarios_array"),
    )


class CostLineItemRecord(UuidPrimaryKey, Base):
    """One line item of an analysis; ``data`` is the whole line (price provenance, assumptions)."""

    __tablename__ = "cost_line_items"

    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    element_id: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "element_id", "resource"),  # paging walks it
        ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["cost_analyses.id", "cost_analyses.project_id"],
            name="fk_cost_line_items_analysis_cost_analyses",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("category", CostCategory), name="category"),
        CheckConstraint(in_values("kind", CostKind), name="kind"),
        CheckConstraint(in_values("status", LineStatus), name="status"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )
