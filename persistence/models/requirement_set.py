import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
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

from .base import Base, CreatedAt, UuidPrimaryKey

MAX_PLANNING_INPUT_BYTES = 16 * 1024 * 1024


class RequirementSetRecord(UuidPrimaryKey, CreatedAt, Base):
    """An immutable, numbered snapshot of a project's requirements (v1, v2, ...): exactly which
    requirement versions an architecture was planned or evaluated against. Append-only (trigger).

    ``planning_input`` is the Architecture Planning Input built at creation and stored as is, so
    the engines' input stays byte-for-byte reproducible even if the code that derives it changes;
    ``content_hash`` is its SHA-256 over canonical JSON (equal content, equal hash).
    """

    __tablename__ = "requirement_sets"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    planning_input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # No foreign key, like requirement_versions: an immutable row cannot be SET NULL.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        # Also serves the RESTRICT check on projects(id) and listing newest first.
        UniqueConstraint("project_id", "number"),
        # Target of the items' foreign key (same-project guarantee).
        UniqueConstraint("id", "project_id"),
        CheckConstraint("number >= 1", name="number_positive"),
        CheckConstraint("char_length(name) <= 100", name="name_length"),
        CheckConstraint("char_length(description) <= 2000", name="description_length"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        CheckConstraint("requirement_count >= 1", name="requirement_count_positive"),
        CheckConstraint("jsonb_typeof(planning_input) = 'object'", name="planning_input_object"),
        CheckConstraint(
            f"octet_length(planning_input::text) <= {MAX_PLANNING_INPUT_BYTES}", name="planning_input_size"
        ),
    )


class RequirementSetItemRecord(Base):
    """One pinned requirement version in a set. Append-only (trigger)."""

    __tablename__ = "requirement_set_items"

    requirement_set_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        # The set, the requirement and the item share one project...
        ForeignKeyConstraint(
            ["requirement_set_id", "project_id"],
            ["requirement_sets.id", "requirement_sets.project_id"],
            name="fk_requirement_set_items_set_requirement_sets",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "project_id"],
            ["requirements.id", "requirements.project_id"],
            name="fk_requirement_set_items_requirement_requirements",
            ondelete="RESTRICT",
        ),
        # ...and the pinned version exists (and, being append-only, never changes).
        ForeignKeyConstraint(
            ["requirement_id", "version"],
            ["requirement_versions.requirement_id", "requirement_versions.version"],
            name="fk_requirement_set_items_version_requirement_versions",
            ondelete="RESTRICT",
        ),
        # Which sets pin a requirement (and the RESTRICT checks of the foreign keys above).
        Index(None, "requirement_id", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
    )
