"""Discovery runs. One row per run: what was requested (source type, label, an optional baseline
revision), who and when, and its stored result — the artifacts (path, hash and size only: never their
content), findings (secrets redacted), candidates, relationships, diagnostics, the proposed
architecture and its validation — or the reason it failed. A run's request and result are written
once; only its review decisions and acceptances change, by people (a trigger refuses anything else).
A run is deleted only while no acceptance refers to it: an accepted run is the provenance of the
revision it created. Nothing here is an architecture: an accepted proposal is stored as a revision."""

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

from core.domain.discovery.values import RunStatus, SourceType

from ._checks import in_values
from .base import Base, Timestamps, UuidPrimaryKey

HASH = "'^[0-9a-f]{64}$'"
MAX_RESULT_BYTES = 32 * 1024 * 1024
RESULTED = "('completed', 'completed_with_warnings')"


class DiscoveryRunRecord(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "discovery_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str | None] = mapped_column(Text)  # as requested; None: detected per artifact
    label: Mapped[str | None] = mapped_column(Text)
    baseline_architecture_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    baseline_revision_number: Mapped[int | None] = mapped_column(Integer)
    # No foreign key, like the other records a person's account must not change: the run keeps who asked.
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )  # the result's counts, for listings
    fingerprint: Mapped[str | None] = mapped_column(Text)  # of the result
    sources_fingerprint: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    decisions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    acceptances: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of drift analyses' same-project foreign keys
        ForeignKeyConstraint(
            ["baseline_architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_discovery_runs_baseline_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["baseline_architecture_id", "baseline_revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_discovery_runs_baseline_architecture_revisions",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("status", RunStatus), name="status"),
        CheckConstraint(f"source_type IS NULL OR {in_values('source_type', SourceType)}", name="source_type"),
        CheckConstraint("label IS NULL OR char_length(label) BETWEEN 1 AND 200", name="label_length"),
        CheckConstraint(
            "(baseline_architecture_id IS NULL) = (baseline_revision_number IS NULL)",
            name="baseline_complete",
        ),
        CheckConstraint(
            f"(status IN {RESULTED}) = (result IS NOT NULL AND summary IS NOT NULL "
            "AND fingerprint IS NOT NULL AND sources_fingerprint IS NOT NULL)",
            name="result_when_completed",
        ),
        CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="error_when_failed"),
        CheckConstraint(f"fingerprint IS NULL OR fingerprint ~ {HASH}", name="fingerprint_format"),
        CheckConstraint(
            f"sources_fingerprint IS NULL OR sources_fingerprint ~ {HASH}",
            name="sources_fingerprint_format",
        ),
        CheckConstraint("result IS NULL OR jsonb_typeof(result) = 'object'", name="result_object"),
        CheckConstraint(
            f"result IS NULL OR octet_length(result::text) <= {MAX_RESULT_BYTES}", name="result_size"
        ),
        CheckConstraint("jsonb_typeof(decisions) = 'array'", name="decisions_array"),
        CheckConstraint("jsonb_typeof(acceptances) = 'array'", name="acceptances_array"),
        # A project's runs, newest first; also serves the RESTRICT check on projects(id).
        Index("ix_discovery_runs_project_listing", "project_id", "requested_at", "id"),
        Index("ix_discovery_runs_baseline", "baseline_architecture_id", "baseline_revision_number"),
    )
