import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.organizations.enums import Role

from ._checks import in_values
from .base import Base, Timestamps, UuidPrimaryKey


class InvitationRecord(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "invitations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Normalized like users.email; acceptance requires the signed-in user's email to match.
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    # SET NULL: an invitation outlives the account of whoever sent or accepted it.
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # At most one pending invitation per email per organization. Also the race guard for
        # two admins inviting the same person at once.
        Index(
            "uq_invitations_pending_email",
            "organization_id",
            func.lower(text("email")),
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
        # Listing an organization's invitations, newest first.
        Index(None, "organization_id", "created_at"),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("email = btrim(email) AND char_length(email) BETWEEN 3 AND 320", name="email_format"),
        CheckConstraint(in_values("role", Role), name="role"),
        CheckConstraint("accepted_at IS NULL OR revoked_at IS NULL", name="single_outcome"),
        CheckConstraint("accepted_by_user_id IS NULL OR accepted_at IS NOT NULL", name="accepted_state"),
    )
