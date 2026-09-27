"""Evolution analyses and their candidates. Both tables are append-only (an analysis is stored once,
finished; triggers refuse updates and deletes), belong to one project, and reference the architecture
revision analyzed through same-project foreign keys. Nothing here is applied to the architecture:
candidates are proposals, stored with their overlay changes, validation, impacts and trade-offs."""

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

from core.domain.evolution.values import CandidateCategory, EvolutionStatus, ValidationState

from ._checks import in_values
from .base import Base, CreatedAt, UuidPrimaryKey

_STATUSES = ", ".join(f"'{s}'" for s in ("pending", "running", *(s.value for s in EvolutionStatus)))


class EvolutionAnalysisRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "evolution_analyses"

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
    # the request (goals, constraints, scope, cited evidence, assumptions), the requirements read,
    # the policy, provider and currency
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_set: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_fingerprint: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    goals: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    findings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    assumptions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of the candidates' same-project foreign key
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_evolution_analyses_architecture_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_evolution_analyses_revision_architecture_revisions",
            ondelete="RESTRICT",
        ),
        Index(None, "architecture_id", "requested_at", "id"),
        CheckConstraint(f"status IN ({_STATUSES})", name="status"),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        CheckConstraint("revision_content_hash ~ '^[0-9a-f]{64}$'", name="revision_content_hash_format"),
        CheckConstraint("label IS NULL OR char_length(label) BETWEEN 1 AND 100", name="label_length"),
        CheckConstraint(
            "status IN ('pending', 'running', 'failed') OR (completed_at IS NOT NULL AND result_fingerprint "
            "IS NOT NULL AND summary IS NOT NULL AND model_set IS NOT NULL)",
            name="finished_has_result",
        ),
        CheckConstraint(
            "status <> 'failed' OR (completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="failed_has_error",
        ),
        CheckConstraint("jsonb_typeof(inputs) = 'object'", name="inputs_object"),
        CheckConstraint("jsonb_typeof(goals) = 'array'", name="goals_array"),
        CheckConstraint("jsonb_typeof(findings) = 'array'", name="findings_array"),
    )


class EvolutionCandidateRecord(UuidPrimaryKey, Base):
    """One candidate of an analysis, at its position in the canonical order; ``data`` is the whole
    candidate (changes, evidence, effects, validation, impacts, consequences)."""

    __tablename__ = "evolution_candidates"

    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    candidate_id: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    validation: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "candidate_id"),
        UniqueConstraint("analysis_id", "position"),
        ForeignKeyConstraint(
            ["analysis_id", "project_id"],
            ["evolution_analyses.id", "evolution_analyses.project_id"],
            name="fk_evolution_candidates_analysis_evolution_analyses",
            ondelete="RESTRICT",
        ),
        CheckConstraint("candidate_id ~ '^evo_[0-9a-f]{20}$'", name="candidate_id_format"),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint(in_values("category", CandidateCategory), name="category"),
        CheckConstraint(in_values("validation", ValidationState), name="validation"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )
