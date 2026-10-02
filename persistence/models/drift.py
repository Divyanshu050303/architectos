"""Drift detection records.

- ``drift_analyses``: one row per analysis — the exact baseline revision and discovery run compared
  (same-project foreign keys), the request, and the result (compatibility, coverage, findings with
  their evidence and impact context) or the error. Append-only: a trigger refuses any change and
  deletion. A discovery run an analysis compared cannot be deleted (RESTRICT).
- ``drift_items``: a difference followed across an architecture's analyses (unique per architecture
  and key). Its identity never changes; only its latest detection, review status, review history,
  links and artifacts do — the history only grows. Never deleted.
- ``drift_identity_mappings``: identities people confirmed between an architecture's nodes and
  discovered entities. Append-only (a retraction is a newer mapping).

Nothing here is an architecture: no revision is written by drift detection.
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
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.drift.values import AnalysisStatus, Classification, ElementType, FindingType, ReviewStatus

from ._checks import in_values
from .base import Base, CreatedAt, Timestamps, UuidPrimaryKey

HASH = "'^[0-9a-f]{64}$'"
MAX_RESULT_BYTES = 32 * 1024 * 1024
RESULTED = "('completed', 'completed_with_warnings', 'incompatible_inputs')"


class DriftAnalysisRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "drift_analyses"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    architecture_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    baseline_revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    discovery_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    fingerprint: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # No foreign key, like the other append-only records: an immutable row keeps who asked.
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of the items' same-project foreign keys
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_drift_analyses_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "baseline_revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_drift_analyses_architecture_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["discovery_run_id", "project_id"],
            ["discovery_runs.id", "discovery_runs.project_id"],
            name="fk_drift_analyses_discovery_runs",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("status", AnalysisStatus), name="status"),
        CheckConstraint(
            f"(status IN {RESULTED}) = "
            "(result IS NOT NULL AND summary IS NOT NULL AND fingerprint IS NOT NULL)",
            name="result_when_compared",
        ),
        CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="error_when_failed"),
        CheckConstraint(f"fingerprint IS NULL OR fingerprint ~ {HASH}", name="fingerprint_format"),
        CheckConstraint("jsonb_typeof(request) = 'object'", name="request_object"),
        CheckConstraint(
            f"result IS NULL OR octet_length(result::text) <= {MAX_RESULT_BYTES}", name="result_size"
        ),
        CheckConstraint("baseline_revision_number >= 1", name="revision_positive"),
        # A project's analyses newest first; also serves the RESTRICT check on projects(id).
        Index("ix_drift_analyses_project_listing", "project_id", "requested_at", "id"),
        Index("ix_drift_analyses_architecture", "architecture_id", "requested_at"),
        Index("ix_drift_analyses_discovery_run", "discovery_run_id"),
    )


class DriftItemRecord(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "drift_items"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    architecture_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    element: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[str] = mapped_column(Text, nullable=False)
    first_analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    last_analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    history: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    links: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    artifacts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("architecture_id", "key"),  # one item per difference of an architecture
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_drift_items_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["first_analysis_id", "project_id"],
            ["drift_analyses.id", "drift_analyses.project_id"],
            name="fk_drift_items_first_analysis",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["last_analysis_id", "project_id"],
            ["drift_analyses.id", "drift_analyses.project_id"],
            name="fk_drift_items_last_analysis",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("element", ElementType), name="element"),
        CheckConstraint(in_values("type", FindingType), name="type"),
        CheckConstraint(in_values("classification", Classification), name="classification"),
        CheckConstraint(in_values("status", ReviewStatus), name="status"),
        CheckConstraint("key ~ '^dfi_[0-9a-f]{20}$'", name="key_format"),
        CheckConstraint(
            "jsonb_typeof(history) = 'array' AND jsonb_array_length(history) >= 1", name="history_array"
        ),
        CheckConstraint("jsonb_typeof(links) = 'array'", name="links_array"),
        CheckConstraint("jsonb_typeof(artifacts) = 'array'", name="artifacts_array"),
        Index("ix_drift_items_listing", "project_id", "architecture_id", "status", "id"),
    )


class DriftIdentityMappingRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "drift_identity_mappings"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    architecture_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    baseline_id: Mapped[str] = mapped_column(Text, nullable=False)
    discovered_key: Mapped[str | None] = mapped_column(Text)  # None: retracted
    confirmed_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_drift_identity_mappings_architectures",
            ondelete="RESTRICT",
        ),
        CheckConstraint("char_length(baseline_id) BETWEEN 1 AND 128", name="baseline_id_length"),
        CheckConstraint(
            "discovered_key IS NULL OR char_length(discovered_key) BETWEEN 1 AND 128",
            name="discovered_key_length",
        ),
        CheckConstraint("note IS NULL OR char_length(note) BETWEEN 1 AND 2000", name="note_length"),
        Index("ix_drift_identity_mappings_architecture", "architecture_id", "confirmed_at"),
    )
