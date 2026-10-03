"""Architecture workflows, their candidates and their steps.

- **A workflow** row is also its job: ``queued`` rows are claimed by a worker, which holds a lease
  (``lease_owner`` and ``lease_expires_at``, present exactly while ``running``). A lease that expires
  lets another worker take the workflow over and resume it from its last completed step. The request
  (goal, budget, who and when) never changes; a finished workflow never changes; none is deleted.
- **A candidate** keeps its architecture (the IR's own JSON) and lineage forever: only its status and
  its engine reports change. The IR is checked against its content hash on read.
- **A step** is written once (append-only): the workflow's checkpoint, unique by its operation key and
  attempt.

Same-project foreign keys tie a workflow to its requirement set, requirement analysis and approved
revision, and a candidate's parent to the same workflow.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
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

from core.domain.architecture_workflow.values import (
    Action,
    CandidateOrigin,
    CandidateStatus,
    Stage,
    StepStatus,
    WorkflowStatus,
)

from ._checks import in_values
from .base import Base, CreatedAt, Timestamps, UuidPrimaryKey

HASH = "'^[0-9a-f]{64}$'"
TERMINAL = "('approved', 'rejected', 'cancelled', 'failed')"
MAX_CANDIDATE_BYTES = 8 * 1024 * 1024


class WorkflowRecord(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "architecture_workflows"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    goal: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    requirement_analysis_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requirement_set_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    input_request: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    answers: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    selected: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    approved_candidate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_architecture_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_revision_number: Mapped[int | None] = mapped_column(Integer)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    history: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of the candidates' and steps' same-project keys
        ForeignKeyConstraint(
            ["requirement_set_id", "project_id"],
            ["requirement_sets.id", "requirement_sets.project_id"],
            name="fk_architecture_workflows_requirement_sets",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["requirement_analysis_id", "project_id"],
            ["requirement_analyses.id", "requirement_analyses.project_id"],
            name="fk_architecture_workflows_requirement_analyses",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["approved_architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_architecture_workflows_approved_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["approved_architecture_id", "approved_revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_architecture_workflows_approved_revisions",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("status", WorkflowStatus), name="status"),
        CheckConstraint(in_values("stage", Stage), name="stage"),
        CheckConstraint("iteration >= 0", name="iteration_non_negative"),
        CheckConstraint(
            "(status = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="lease_when_running",
        ),
        CheckConstraint(
            "lease_owner IS NULL OR char_length(lease_owner) BETWEEN 1 AND 128", name="lease_owner_length"
        ),
        CheckConstraint("(status = 'needs_input') = (input_request IS NOT NULL)", name="input_when_waiting"),
        CheckConstraint("(status = 'failed') = (failure IS NOT NULL)", name="failure_when_failed"),
        CheckConstraint("(status = 'rejected') = (decision_reason IS NOT NULL)", name="reason_when_rejected"),
        CheckConstraint(
            "(status = 'approved') = (approved_candidate_id IS NOT NULL "
            "AND approved_architecture_id IS NOT NULL AND approved_revision_number IS NOT NULL)",
            name="revision_when_approved",
        ),
        CheckConstraint(
            f"(status IN {TERMINAL}) = (completed_at IS NOT NULL)", name="completed_when_finished"
        ),
        CheckConstraint(
            "decision_reason IS NULL OR char_length(decision_reason) BETWEEN 1 AND 2000",
            name="decision_reason_length",
        ),
        CheckConstraint("jsonb_typeof(goal) = 'object'", name="goal_object"),
        CheckConstraint("jsonb_typeof(budget) = 'object'", name="budget_object"),
        CheckConstraint("jsonb_typeof(usage) = 'object'", name="usage_object"),
        CheckConstraint("jsonb_typeof(answers) = 'array'", name="answers_array"),
        CheckConstraint("jsonb_typeof(selected) = 'array'", name="selected_array"),
        CheckConstraint("jsonb_typeof(history) = 'array'", name="history_array"),
        CheckConstraint("jsonb_typeof(limitations) = 'array'", name="limitations_array"),
        CheckConstraint(
            "input_request IS NULL OR jsonb_typeof(input_request) = 'object'", name="input_request_object"
        ),
        CheckConstraint("failure IS NULL OR jsonb_typeof(failure) = 'object'", name="failure_object"),
        # A project's workflows, newest first; also serves the RESTRICT check on projects(id).
        Index("ix_architecture_workflows_project_listing", "project_id", "requested_at", "id"),
        # What a worker can claim: queued ones, and running ones whose lease expired.
        Index("ix_architecture_workflows_claimable", "status", "lease_expires_at"),
        Index("ix_architecture_workflows_requirement_set", "requirement_set_id", "project_id"),
        Index("ix_architecture_workflows_requirement_analysis", "requirement_analysis_id", "project_id"),
        Index("ix_architecture_workflows_approved", "approved_architecture_id", "approved_revision_number"),
    )


class WorkflowCandidateRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "workflow_candidates"

    workflow_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    ir: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    rule: Mapped[str | None] = mapped_column(Text)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reports: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    assumptions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("workflow_id", "ordinal"),
        UniqueConstraint("id", "workflow_id"),  # target of the parent and step foreign keys
        ForeignKeyConstraint(
            ["workflow_id", "project_id"],
            ["architecture_workflows.id", "architecture_workflows.project_id"],
            name="fk_workflow_candidates_workflows",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parent_id", "workflow_id"],
            ["workflow_candidates.id", "workflow_candidates.workflow_id"],
            name="fk_workflow_candidates_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("origin", CandidateOrigin), name="origin"),
        CheckConstraint(in_values("status", CandidateStatus), name="status"),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint("(ordinal = 1) = (parent_id IS NULL)", name="parent_unless_first"),
        CheckConstraint("(origin = 'rule') = (rule IS NOT NULL)", name="rule_when_by_rule"),
        CheckConstraint(f"content_hash ~ {HASH}", name="content_hash_format"),
        CheckConstraint("char_length(reason) BETWEEN 1 AND 500", name="reason_length"),
        CheckConstraint("jsonb_typeof(ir) = 'object'", name="ir_object"),
        CheckConstraint("jsonb_typeof(reports) = 'array'", name="reports_array"),
        CheckConstraint("trigger IS NULL OR jsonb_typeof(trigger) = 'object'", name="trigger_object"),
        CheckConstraint(f"octet_length(ir::text || reports::text) <= {MAX_CANDIDATE_BYTES}", name="size"),
        Index("ix_workflow_candidates_workflow", "workflow_id", "project_id", "ordinal"),
        Index("ix_workflow_candidates_parent", "parent_id", "workflow_id"),
    )


class WorkflowStepRecord(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "workflow_steps"

    workflow_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("workflow_id", "key", "attempt"),  # an operation is attempted at most once a try
        UniqueConstraint("workflow_id", "ordinal"),
        ForeignKeyConstraint(
            ["workflow_id", "project_id"],
            ["architecture_workflows.id", "architecture_workflows.project_id"],
            name="fk_workflow_steps_workflows",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "workflow_id"],
            ["workflow_candidates.id", "workflow_candidates.workflow_id"],
            name="fk_workflow_steps_candidates",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("action", Action), name="action"),
        CheckConstraint(in_values("stage", Stage), name="stage"),
        CheckConstraint(in_values("status", StepStatus), name="status"),
        CheckConstraint("attempt BETWEEN 1 AND 2", name="attempt_bounded"),
        CheckConstraint("(status = 'completed') = (error IS NULL)", name="error_unless_completed"),
        CheckConstraint("NOT retryable OR status = 'failed'", name="retryable_only_when_failed"),
        CheckConstraint("completed_at >= started_at", name="completed_after_started"),
        CheckConstraint("jsonb_typeof(outputs) = 'object'", name="outputs_object"),
        CheckConstraint("jsonb_typeof(usage) = 'object'", name="usage_object"),
        Index("ix_workflow_steps_workflow", "workflow_id", "project_id", "ordinal"),
        Index("ix_workflow_steps_candidate", "candidate_id", "workflow_id"),
    )
