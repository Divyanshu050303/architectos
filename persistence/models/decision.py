"""Architecture decision records. A decision belongs to one project (and one architecture of it); its
number is unique in the project (ADR-<number>). Unlike analyses, a decision changes over its
lifecycle — proposed, then accepted or rejected, then (if accepted) superseded or linked to the
revision a person says implements it — so the row is updated; every change is audited. Decisions are
never deleted. The options are snapshots of the candidates considered, never the architecture."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Integer, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.decisions.entities import DecisionStatus

from ._checks import in_values
from .base import Base, Timestamps, UuidPrimaryKey


class DecisionRecord(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "decisions"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    architecture_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # the evolution analysis and baseline
    goals: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    assumptions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    related_element_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    chosen_option: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    resulting_revision_number: Mapped[int | None] = mapped_column(Integer)
    resulting_content_hash: Mapped[str | None] = mapped_column(Text)
    linked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("id", "project_id"),  # target of superseded_by's same-project foreign key
        UniqueConstraint("project_id", "number"),
        ForeignKeyConstraint(
            ["architecture_id", "project_id"],
            ["architectures.id", "architectures.project_id"],
            name="fk_decisions_architecture_architectures",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["superseded_by", "project_id"],
            ["decisions.id", "decisions.project_id"],
            name="fk_decisions_superseded_by_decisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["architecture_id", "resulting_revision_number"],
            ["architecture_revisions.architecture_id", "architecture_revisions.number"],
            name="fk_decisions_resulting_revision_architecture_revisions",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_values("status", DecisionStatus), name="status"),
        CheckConstraint("number >= 1", name="number_positive"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="title_length"),
        CheckConstraint("(status = 'proposed') = (decided_at IS NULL)", name="decided_unless_proposed"),
        CheckConstraint(
            "status NOT IN ('accepted', 'superseded') OR chosen_option IS NOT NULL",
            name="accepted_has_option",
        ),
        CheckConstraint(
            "(status = 'superseded') = (superseded_by IS NOT NULL)", name="superseded_has_successor"
        ),
        CheckConstraint("(resulting_revision_number IS NULL) = (linked_at IS NULL)", name="link_complete"),
        CheckConstraint("jsonb_typeof(options) = 'array'", name="options_array"),
    )
