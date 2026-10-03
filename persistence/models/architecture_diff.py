"""Architecture diffs and their explanation runs. Both append-only (a trigger refuses any UPDATE,
DELETE or TRUNCATE): a diff is what was compared, as it was; another explanation is another run.

A diff row names its two states exactly — a revision (same-project foreign keys to the architecture
and the revision) or an agent run's candidate (a same-project foreign key to the run) — with each
state's content hash, never a copy of either architecture. Its parts (the semantic diff, impacts and
the engines' comparison) are JSON documents read back through the domain's constructors.

An explanation row keeps the model, prompt version and usage, the SHA-256 and size of the model's
output (never the output, the prompt or the retrieved text), the validated explanation or why there is
none, and the citations of the passages it cites."""

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

from core.domain.architecture_diff.values import ExplanationFailure, ExplanationStatus, StateKind

from ._checks import in_values
from .base import Base, CreatedAt, UuidPrimaryKey

HASH = "'^[0-9a-f]{64}$'"
MAX_PARTS_BYTES = 16 * 1024 * 1024
MAX_EXPLANATION_BYTES = 1024 * 1024


def _side_checks(side: str) -> tuple[CheckConstraint, ...]:
    revision = (
        f"{side}_architecture_id IS NOT NULL AND {side}_revision_number IS NOT NULL AND {side}_run_id IS NULL"
    )
    candidate = (
        f"{side}_run_id IS NOT NULL AND {side}_architecture_id IS NULL AND {side}_revision_number IS NULL"
    )
    return (
        CheckConstraint(in_values(f"{side}_kind", StateKind), name=f"{side}_kind"),
        CheckConstraint(
            f"({side}_kind = 'revision' AND {revision}) OR ({side}_kind = 'candidate' AND {candidate})",
            name=f"{side}_reference",
        ),
        CheckConstraint(f"{side}_content_hash ~ {HASH}", name=f"{side}_content_hash_format"),
        CheckConstraint(f"char_length({side}_label) BETWEEN 1 AND 200", name=f"{side}_label_length"),
    )


def _side_keys(side: str) -> tuple[ForeignKeyConstraint, ...]:
    return (
        ForeignKeyConstraint(
            [f"{side}_architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name=f"fk_architecture_diffs_{side}_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [f"{side}_architecture_id", f"{side}_revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name=f"fk_architecture_diffs_{side}_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [f"{side}_run_id", "project_id"],
            ["architecture_agent_runs.id", "architecture_agent_runs.project_id"],
            name=f"fk_architecture_diffs_{side}_agent_runs",
            ondelete="RESTRICT",
        ),
    )


class ArchitectureDiffRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "architecture_diffs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    compared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    base_kind: Mapped[str] = mapped_column(Text, nullable=False)
    base_architecture_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    base_revision_number: Mapped[int | None] = mapped_column(Integer)
    base_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    base_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    base_label: Mapped[str] = mapped_column(Text, nullable=False)
    target_kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_architecture_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    target_revision_number: Mapped[int | None] = mapped_column(Integer)
    target_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    target_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    target_label: Mapped[str] = mapped_column(Text, nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    semantic: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_count: Mapped[int] = mapped_column(Integer, nullable=False)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    requirements: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    decisions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    engines: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    unknowns: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of the explanations' same-project foreign key
        *_side_keys("base"),
        *_side_keys("target"),
        *_side_checks("base"),
        *_side_checks("target"),
        CheckConstraint("change_count >= 0", name="change_count_non_negative"),
        CheckConstraint("jsonb_typeof(request) = 'object'", name="request_object"),
        CheckConstraint("jsonb_typeof(semantic) = 'object'", name="semantic_object"),
        CheckConstraint("jsonb_typeof(counts) = 'object'", name="counts_object"),
        CheckConstraint("jsonb_typeof(requirements) = 'array'", name="requirements_array"),
        CheckConstraint("jsonb_typeof(decisions) = 'array'", name="decisions_array"),
        CheckConstraint("jsonb_typeof(engines) = 'array'", name="engines_array"),
        CheckConstraint("jsonb_typeof(warnings) = 'array'", name="warnings_array"),
        CheckConstraint("jsonb_typeof(unknowns) = 'array'", name="unknowns_array"),
        CheckConstraint(
            "octet_length(semantic::text || requirements::text || decisions::text || engines::text) "
            f"<= {MAX_PARTS_BYTES}",
            name="parts_size",
        ),
        # A project's diffs, newest first; also serves the RESTRICT check on projects(id).
        Index("ix_architecture_diffs_project_listing", "project_id", "compared_at", "id"),
        Index("ix_architecture_diffs_base_revision", "base_architecture_id", "base_revision_number"),
        Index("ix_architecture_diffs_target_revision", "target_architecture_id", "target_revision_number"),
        Index("ix_architecture_diffs_base_run", "base_run_id"),
        Index("ix_architecture_diffs_target_run", "target_run_id"),
    )


class DiffExplanationRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "architecture_diff_explanations"

    diff_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_output_sha256: Mapped[str | None] = mapped_column(Text)
    raw_output_bytes: Mapped[int | None] = mapped_column(Integer)
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    failure: Mapped[str | None] = mapped_column(Text)
    rejections: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["diff_id", "project_id"],
            ["architecture_diffs.id", "architecture_diffs.project_id"],
            name="fk_architecture_diff_explanations_diffs",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("status", ExplanationStatus), name="status"),
        CheckConstraint(f"failure IS NULL OR {in_values('failure', ExplanationFailure)}", name="failure"),
        CheckConstraint(
            "(status = 'completed') = (explanation IS NOT NULL)", name="explanation_when_completed"
        ),
        CheckConstraint("(status = 'failed') = (failure IS NOT NULL)", name="failure_when_failed"),
        CheckConstraint(
            "status <> 'not_needed' OR (model IS NULL AND raw_output_sha256 IS NULL)",
            name="no_model_when_not_needed",
        ),
        CheckConstraint(
            "(raw_output_sha256 IS NULL) = (raw_output_bytes IS NULL)", name="raw_output_complete"
        ),
        CheckConstraint(
            f"raw_output_sha256 IS NULL OR (raw_output_sha256 ~ {HASH} AND raw_output_bytes >= 0)",
            name="raw_output_format",
        ),
        CheckConstraint("model IS NULL OR char_length(model) BETWEEN 1 AND 128", name="model_length"),
        CheckConstraint(
            "prompt_version IS NULL OR char_length(prompt_version) BETWEEN 1 AND 64",
            name="prompt_version_length",
        ),
        CheckConstraint("jsonb_typeof(usage) = 'object'", name="usage_object"),
        CheckConstraint(
            "explanation IS NULL OR jsonb_typeof(explanation) = 'object'", name="explanation_object"
        ),
        CheckConstraint("jsonb_typeof(evidence) = 'array'", name="evidence_array"),
        CheckConstraint("jsonb_typeof(rejections) = 'array'", name="rejections_array"),
        CheckConstraint("jsonb_typeof(limitations) = 'array'", name="limitations_array"),
        CheckConstraint(
            f"octet_length(coalesce(explanation::text, '')) <= {MAX_EXPLANATION_BYTES}",
            name="explanation_size",
        ),
        # A diff's runs, oldest first.
        Index("ix_architecture_diff_explanations_diff", "diff_id", "project_id", "requested_at", "id"),
    )
