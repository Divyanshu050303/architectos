import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAt, UuidPrimaryKey


class PasswordResetTokenRecord(UuidPrimaryKey, CreatedAt, Base):
    """Single-use, expiring token. Only SHA-256 of the emailed value is stored."""

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when a newer token is issued for the same user, so only the latest link works.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Outstanding tokens of a user, for revoking them when a new one is issued.
        Index(
            "ix_password_reset_tokens_user_id_outstanding",
            "user_id",
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("consumed_at IS NULL OR revoked_at IS NULL", name="single_outcome"),
    )
