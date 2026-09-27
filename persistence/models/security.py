"""Security analyses, their components and findings. All three tables are append-only (an analysis
is stored once, finished; triggers refuse updates and deletes), belong to one project, and reference
the architecture revision they analyzed through same-project foreign keys. Nothing here holds a
secret: findings and components carry element ids, closed-set values and redacted setting names."""

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
from core.domain.security.results import (
    TYPES,
    Coverage,
    FindingBasis,
    FindingCategory,
    FindingType,
    SecurityStatus,
    StrideCategory,
)
from core.domain.validation.results import Severity

from ._checks import in_values
from .base import Base, CreatedAt, UuidPrimaryKey

_STATUSES = ", ".join(f"'{s}'" for s in ("pending", "running", *(s.value for s in SecurityStatus)))
# A finding's type fixes its category and basis: the table refuses any other pairing.
_CLASSIFIED = " OR ".join(
    f"(type = '{t.value}' AND category = '{c.value}' AND basis = '{b.value}')"
    for t, (c, b) in sorted(TYPES.items(), key=lambda item: item[0].value)
)


class SecurityAnalysisRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "security_analyses"

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
    trust_zones: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
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
            name="fk_security_analyses_architecture_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_security_analyses_revision_architecture_revisions",
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
        CheckConstraint("jsonb_typeof(trust_zones) = 'array'", name="trust_zones_array"),
        CheckConstraint("jsonb_typeof(checks) = 'array'", name="checks_array"),
    )


class SecurityComponentRecord(UuidPrimaryKey, Base):
    """One component's modeled controls in an analysis; ``data`` is the whole result."""

    __tablename__ = "security_components"

    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    coverage: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "node_id"),
        ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["security_analyses.id", "security_analyses.project_id"],
            name="fk_security_components_analysis_security_analyses",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("coverage", Coverage), name="coverage"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )


class SecurityFindingRecord(UuidPrimaryKey, Base):
    """One finding of an analysis, at its position in the canonical order."""

    __tablename__ = "security_findings"

    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_id: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    certainty: Mapped[str] = mapped_column(Text, nullable=False)
    threat: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "position"),
        UniqueConstraint("analysis_id", "finding_id"),
        ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["security_analyses.id", "security_analyses.project_id"],
            name="fk_security_findings_analysis_security_analyses",
            ondelete="RESTRICT",
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("finding_id ~ '^sec_[0-9a-f]{16}$'", name="finding_id_format"),
        CheckConstraint(in_values("type", FindingType), name="type"),
        CheckConstraint(in_values("category", FindingCategory), name="category"),
        CheckConstraint(in_values("basis", FindingBasis), name="basis"),
        CheckConstraint(_CLASSIFIED, name="type_fixes_category_and_basis"),
        CheckConstraint(in_values("severity", Severity), name="severity"),
        CheckConstraint(in_values("certainty", Certainty), name="certainty"),
        CheckConstraint(
            f"(type = 'threat_candidate') = (threat IS NOT NULL) AND "
            f"(threat IS NULL OR {in_values('threat', StrideCategory)})",
            name="threat",
        ),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )
