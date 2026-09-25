import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.requirements.analyses import MAX_INPUT_CHARACTERS

from .base import Base, CreatedAt, UuidPrimaryKey

MAX_RESULT_BYTES = 4 * 1024 * 1024


class RequirementAnalysisRecord(UuidPrimaryKey, CreatedAt, Base):
    """One run of the Requirements Engine over a person's text. Append-only (trigger): the raw input
    is kept exactly as written, so every requirement promoted from it stays explainable."""

    __tablename__ = "requirement_analyses"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    raw_input: Mapped[str] = mapped_column(Text, nullable=False)
    input_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # No foreign key, like the other append-only tables: an immutable row cannot be SET NULL.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        # Target of the requirements' origin foreign key (same-project guarantee).
        UniqueConstraint("id", "project_id"),
        # A project's analyses, newest first; also serves the RESTRICT check on projects(id).
        Index(None, "project_id", "created_at", "id"),
        CheckConstraint(
            f"char_length(raw_input) BETWEEN 1 AND {MAX_INPUT_CHARACTERS}", name="raw_input_length"
        ),
        CheckConstraint("input_sha256 ~ '^[0-9a-f]{64}$'", name="input_sha256_format"),
        CheckConstraint("char_length(engine_version) BETWEEN 1 AND 100", name="engine_version_length"),
        CheckConstraint("jsonb_typeof(result) = 'object'", name="result_object"),
        CheckConstraint(f"octet_length(result::text) <= {MAX_RESULT_BYTES}", name="result_size"),
    )
