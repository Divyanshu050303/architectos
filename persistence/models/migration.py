"""Migration plan versions. A plan is the sequence of its versions (same ``plan_id``); each version is
one immutable proposal — steps and their dependencies, risks, data migrations, downtime and
compatibility, checkpoints, rollback considerations, findings, evidence, rule and model versions —
with the request it answered, between an exact source revision and an exact target (a later
revision, or an evolution candidate on the source revision). Only its review status and review
history change, by people: a trigger refuses any other change, and versions are never deleted.
The architecture is never copied: the revisions are referenced."""

import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.migrations.values import PlanStatus, TargetKind

from ._checks import in_values
from .base import Base, Timestamps, UuidPrimaryKey

HASH = "'^[0-9a-f]{64}$'"


class MigrationPlanVersionRecord(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "migration_plan_versions"

    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    architecture_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    target_kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    target_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    target_analysis_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # an evolution analysis
    target_candidate_id: Mapped[str | None] = mapped_column(Text)
    strategy: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    reviews: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    __table_args__ = (
        UniqueConstraint("plan_id", "version"),
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_migration_plan_versions_architecture_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "source_revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_migration_plan_versions_source_architecture_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "target_revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_migration_plan_versions_target_architecture_revisions",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("status", PlanStatus), name="status"),
        CheckConstraint(in_values("target_kind", TargetKind), name="target_kind"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(f"source_content_hash ~ {HASH}", name="source_content_hash_format"),
        CheckConstraint(f"target_content_hash ~ {HASH}", name="target_content_hash_format"),
        CheckConstraint(f"fingerprint ~ {HASH}", name="fingerprint_format"),
        CheckConstraint(
            "(target_kind = 'candidate') = "
            "(target_candidate_id IS NOT NULL AND target_analysis_id IS NOT NULL)",
            name="candidate_target_complete",
        ),
        CheckConstraint(
            "target_kind = 'candidate' OR target_revision_number > source_revision_number",
            name="target_after_source",
        ),
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="title_length"),
        CheckConstraint("jsonb_typeof(proposal) = 'object'", name="proposal_object"),
        CheckConstraint("jsonb_typeof(request) = 'object'", name="request_object"),
        CheckConstraint("jsonb_typeof(reviews) = 'array'", name="reviews_array"),
        Index("ix_migration_plan_versions_project_listing", "project_id", "created_at", "id"),
    )
