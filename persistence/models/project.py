import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.projects.enums import ProjectStatus

from ._checks import in_values
from .base import Base, Timestamps, UuidPrimaryKey

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class ProjectRecord(UuidPrimaryKey, Timestamps, Base):
    """An architecture initiative owned by exactly one organization.

    Lifecycle: active -> archived (read-only) -> deleted (soft; only from archived). Nothing is
    purged here, so requirement and architecture history is never lost by accident.
    """

    __tablename__ = "projects"

    # RESTRICT: an organization that still owns projects cannot be hard-deleted, so project history
    # cannot disappear through a cascade. Organizations are soft-deleted in normal operation.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # URL-safe and immutable after creation, so links to a project keep working.
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=ProjectStatus.ACTIVE.value)
    # Typed and validated by the domain (ProjectSettings); the database only guarantees an object.
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # SET NULL: a project outlives the account of whoever created it.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Slug uniqueness per organization, among projects that are not deleted: two organizations
        # may both have "food-delivery", and a deleted project's slug can be reused. Also serves
        # lookups by (organization_id, slug).
        Index(
            "uq_projects_organization_id_slug_live",
            "organization_id",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Project listing, newest first, keyset on (created_at, id), with or without the status
        # filter (two statuses: filtering while walking the index is cheap). Status is not a key
        # column: in the middle of the key it would stop the index from providing the order of
        # the default, unfiltered listing. Deliberately not partial, so it also serves the
        # RESTRICT check on organizations(id), which must see deleted rows too.
        Index(None, "organization_id", "created_at", "id"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name_length"),
        CheckConstraint(
            f"char_length(slug) BETWEEN 1 AND 63 AND slug ~ '{SLUG_PATTERN}'", name="slug_format"
        ),
        CheckConstraint("char_length(description) <= 2000", name="description_length"),
        CheckConstraint(in_values("status", ProjectStatus), name="status"),
        CheckConstraint("jsonb_typeof(settings) = 'object'", name="settings_object"),
        CheckConstraint("(status = 'archived') = (archived_at IS NOT NULL)", name="archived_state"),
        # Deletion is only possible from the archived state (archive first, then delete).
        CheckConstraint("deleted_at IS NULL OR status = 'archived'", name="deleted_requires_archived"),
    )
