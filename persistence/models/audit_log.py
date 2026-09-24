import uuid
from typing import Any

from sqlalchemy import CheckConstraint, Index, Text, Uuid, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAt, UuidPrimaryKey


class AuditLogRecord(UuidPrimaryKey, CreatedAt, Base):
    """Append-only security audit trail.

    No foreign keys on purpose: entries must survive deletion of the organization, actor or
    resource they describe, and must never be rewritten by a cascade. A database trigger
    (see the migration) rejects UPDATE and DELETE.
    """

    __tablename__ = "audit_logs"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    # "metadata" is reserved on declarative classes, hence the attribute name.
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # GET /organizations/{id}/audit-log: newest first, keyset-paginated on (created_at, id).
        Index(
            "ix_audit_logs_organization_id_created_at",
            "organization_id",
            "created_at",
            "id",
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
        # A user's own security history (logins, password changes), which has no organization.
        Index(None, "actor_user_id", "created_at"),
        CheckConstraint(r"action ~ '^[a-z_]+\.[a-z_]+$'", name="action_format"),
        CheckConstraint("char_length(user_agent) <= 512", name="user_agent_length"),
    )
