"""Architecture agent runs. One row per run: what a person asked (the requirement set, an optional base
revision, the request and budget), who and when, where the run stands, what it used, its questions
and answers, the validated proposal, the candidate architecture and the engines' reports — or why it
failed — and, once a person decided, the revision it became or why it was rejected.

Never stored: the prompt, the context, retrieved text or the model's raw output (only the output's
SHA-256 and size). A run's request never changes; a finished run (failed, cancelled, accepted,
rejected) never changes again; a candidate, once there, is the one reviewed (a trigger refuses
anything else). An accepted run is kept: it is the provenance of the revision it created. A stored
run is never ``queued`` or ``running``: a pass ends before its run is stored."""

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
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.architecture_agent.values import RunStatus, Stage

from ._checks import in_values
from .base import Base, Timestamps, UuidPrimaryKey

HASH = "'^[0-9a-f]{64}$'"
WITH_CANDIDATE = "('candidate_ready', 'accepted', 'rejected')"
MAX_PARTS_BYTES = 8 * 1024 * 1024


class AgentRunRecord(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "architecture_agent_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requirement_set_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    base_architecture_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    base_revision_number: Mapped[int | None] = mapped_column(Integer)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    questions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    answers: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    rejections: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    candidate: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    candidate_content_hash: Mapped[str | None] = mapped_column(Text)
    reports: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    raw_output_sha256: Mapped[str | None] = mapped_column(Text)
    raw_output_bytes: Mapped[int | None] = mapped_column(Integer)
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    accepted_architecture_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    accepted_revision_number: Mapped[int | None] = mapped_column(Integer)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    history: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["requirement_set_id", "project_id"],
            ["requirement_sets.id", "requirement_sets.project_id"],
            name="fk_architecture_agent_runs_requirement_sets",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["base_architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_architecture_agent_runs_base_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["base_architecture_id", "base_revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_architecture_agent_runs_base_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["accepted_architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_architecture_agent_runs_accepted_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["accepted_architecture_id", "accepted_revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_architecture_agent_runs_accepted_revisions",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("status", RunStatus), name="status"),
        CheckConstraint("status NOT IN ('queued', 'running')", name="stored_after_a_pass"),
        CheckConstraint(in_values("stage", Stage), name="stage"),
        CheckConstraint(
            "(base_architecture_id IS NULL) = (base_revision_number IS NULL)", name="base_complete"
        ),
        CheckConstraint(
            f"(status IN {WITH_CANDIDATE}) = (candidate IS NOT NULL AND candidate_content_hash IS NOT NULL)",
            name="candidate_when_ready",
        ),
        CheckConstraint("(status = 'failed') = (failure IS NOT NULL)", name="failure_when_failed"),
        CheckConstraint(
            "(status = 'accepted') = "
            "(accepted_architecture_id IS NOT NULL AND accepted_revision_number IS NOT NULL)",
            name="revision_when_accepted",
        ),
        CheckConstraint("(status = 'rejected') = (decision_reason IS NOT NULL)", name="reason_when_rejected"),
        CheckConstraint(
            "decision_reason IS NULL OR char_length(decision_reason) BETWEEN 1 AND 2000",
            name="decision_reason_length",
        ),
        CheckConstraint(
            "(raw_output_sha256 IS NULL) = (raw_output_bytes IS NULL)", name="raw_output_complete"
        ),
        CheckConstraint(
            f"raw_output_sha256 IS NULL OR (raw_output_sha256 ~ {HASH} AND raw_output_bytes >= 0)",
            name="raw_output_format",
        ),
        CheckConstraint(
            f"candidate_content_hash IS NULL OR candidate_content_hash ~ {HASH}",
            name="candidate_hash_format",
        ),
        CheckConstraint("model IS NULL OR char_length(model) BETWEEN 1 AND 128", name="model_length"),
        CheckConstraint(
            "prompt_version IS NULL OR char_length(prompt_version) BETWEEN 1 AND 64",
            name="prompt_version_length",
        ),
        CheckConstraint("jsonb_typeof(request) = 'object'", name="request_object"),
        CheckConstraint("jsonb_typeof(budget) = 'object'", name="budget_object"),
        CheckConstraint("jsonb_typeof(usage) = 'object'", name="usage_object"),
        CheckConstraint("jsonb_typeof(questions) = 'array'", name="questions_array"),
        CheckConstraint("jsonb_typeof(answers) = 'array'", name="answers_array"),
        CheckConstraint("jsonb_typeof(rejections) = 'array'", name="rejections_array"),
        CheckConstraint("jsonb_typeof(reports) = 'array'", name="reports_array"),
        CheckConstraint("jsonb_typeof(history) = 'array'", name="history_array"),
        CheckConstraint("jsonb_typeof(limitations) = 'array'", name="limitations_array"),
        CheckConstraint("proposal IS NULL OR jsonb_typeof(proposal) = 'object'", name="proposal_object"),
        CheckConstraint("candidate IS NULL OR jsonb_typeof(candidate) = 'object'", name="candidate_object"),
        CheckConstraint("failure IS NULL OR jsonb_typeof(failure) = 'object'", name="failure_object"),
        CheckConstraint(
            "octet_length(coalesce(proposal::text, '') || coalesce(candidate::text, '') || reports::text) "
            f"<= {MAX_PARTS_BYTES}",
            name="parts_size",
        ),
        # A project's runs, newest first; also serves the RESTRICT check on projects(id).
        Index("ix_architecture_agent_runs_project_listing", "project_id", "requested_at", "id"),
        Index("ix_architecture_agent_runs_requirement_set", "requirement_set_id", "project_id"),
        Index("ix_architecture_agent_runs_base", "base_architecture_id", "base_revision_number"),
        Index("ix_architecture_agent_runs_accepted", "accepted_architecture_id", "accepted_revision_number"),
    )
