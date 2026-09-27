"""Observability analyses, their components and findings. All three tables are append-only (an
analysis is stored once, finished; triggers refuse updates and deletes), belong to one project, and
reference the architecture revision they analyzed through same-project foreign keys. Nothing here is
telemetry: rows hold what the architecture declares (element ids, closed-set values, redacted setting
names) and what the engine concluded from it."""

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

from core.domain.capacity.results import Certainty
from core.domain.observability.results import (
    TYPES,
    FindingBasis,
    FindingCategory,
    FindingType,
    ObservabilityStatus,
)
from core.domain.observability.values import CoverageState, Dimension
from core.domain.validation.results import Severity

from ._checks import in_values
from .base import Base, CreatedAt, UuidPrimaryKey

_STATUSES = ", ".join(f"'{s}'" for s in ("pending", "running", *(s.value for s in ObservabilityStatus)))
# A finding's type fixes its category and basis: the table refuses any other pairing.
_CLASSIFIED = " OR ".join(
    f"(type = '{t.value}' AND category = '{c.value}' AND basis = '{b.value}')"
    for t, (c, b) in sorted(TYPES.items(), key=lambda item: item[0].value)
)


class ObservabilityAnalysisRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "observability_analyses"

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
    # the request, the policy snapshot and the requirements read
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    analyzer_set: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    context_fingerprint: Mapped[str | None] = mapped_column(Text)
    result_fingerprint: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    checks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    unsupported: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of the rows' same-project foreign keys
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_observability_analyses_architecture_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_observability_analyses_revision_architecture_revisions",
            ondelete="RESTRICT",
        ),
        Index(None, "architecture_id", "requested_at", "id"),
        CheckConstraint(f"status IN ({_STATUSES})", name="status"),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        CheckConstraint("revision_content_hash ~ '^[0-9a-f]{64}$'", name="revision_content_hash_format"),
        CheckConstraint("label IS NULL OR char_length(label) BETWEEN 1 AND 100", name="label_length"),
        CheckConstraint(
            "status IN ('pending', 'running', 'failed') OR (completed_at IS NOT NULL "
            "AND result_fingerprint IS NOT NULL AND summary IS NOT NULL AND analyzer_set IS NOT NULL)",
            name="finished_has_result",
        ),
        CheckConstraint(
            "status <> 'failed' OR (completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="failed_has_error",
        ),
        CheckConstraint("jsonb_typeof(inputs) = 'object'", name="inputs_object"),
        CheckConstraint("jsonb_typeof(checks) = 'array'", name="checks_array"),
    )


class ObservabilityComponentRecord(UuidPrimaryKey, Base):
    """One component's coverage in an analysis, a column per dimension (for filtering); ``data`` is
    the whole result."""

    __tablename__ = "observability_components"

    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    criticality: Mapped[str | None] = mapped_column(Text)  # null: not modeled
    logging: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[str] = mapped_column(Text, nullable=False)
    tracing: Mapped[str] = mapped_column(Text, nullable=False)
    health_checks: Mapped[str] = mapped_column(Text, nullable=False)
    alerting: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "node_id"),
        ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["observability_analyses.id", "observability_analyses.project_id"],
            name="fk_observability_components_analysis_observability_analyses",
            ondelete="RESTRICT",
        ),
        CheckConstraint("criticality IS NULL OR criticality IN ('critical', 'standard')", name="criticality"),
        *(CheckConstraint(in_values(d.value, CoverageState), name=d.value) for d in Dimension),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )


class ObservabilityFindingRecord(UuidPrimaryKey, Base):
    """One finding of an analysis, at its position in the priority order."""

    __tablename__ = "observability_findings"

    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_id: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    certainty: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "position"),
        UniqueConstraint("analysis_id", "finding_id"),
        ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["observability_analyses.id", "observability_analyses.project_id"],
            name="fk_observability_findings_analysis_observability_analyses",
            ondelete="RESTRICT",
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("finding_id ~ '^obs_[0-9a-f]{16}$'", name="finding_id_format"),
        CheckConstraint(in_values("type", FindingType), name="type"),
        CheckConstraint(in_values("category", FindingCategory), name="category"),
        CheckConstraint(in_values("basis", FindingBasis), name="basis"),
        CheckConstraint(_CLASSIFIED, name="type_fixes_category_and_basis"),
        CheckConstraint(in_values("severity", Severity), name="severity"),
        CheckConstraint(in_values("certainty", Certainty), name="certainty"),
        CheckConstraint(f"dimension IS NULL OR {in_values('dimension', Dimension)}", name="dimension"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )
