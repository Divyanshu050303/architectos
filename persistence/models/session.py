import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, Text, Uuid, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.identity.enums import SessionRevocationReason

from ._checks import in_values
from .base import Base, Timestamps, UuidPrimaryKey


class SessionRecord(UuidPrimaryKey, Timestamps, Base):
    """A signed-in device. The refresh token is "<session id>.<secret>"; only SHA-256(secret) is stored.

    Lookups go by primary key (the id is inside the token), so the hash needs no index.
    """

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # The hash rotated out by the last refresh, kept only to tell a benign concurrent refresh
    # (within the grace window after refreshed_at) apart from token reuse.
    previous_refresh_token_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)

    __table_args__ = (
        # Active sessions of a user: listing them and revoking them all (password change/reset).
        # Partial, because revoked sessions accumulate and are never read by these queries.
        Index("ix_sessions_user_id_active", "user_id", postgresql_where=text("revoked_at IS NULL")),
        CheckConstraint("octet_length(refresh_token_hash) = 32", name="refresh_token_hash_length"),
        CheckConstraint("(revoked_at IS NULL) = (revoked_reason IS NULL)", name="revocation_state"),
        CheckConstraint(
            f"revoked_reason IS NULL OR {in_values('revoked_reason', SessionRevocationReason)}",
            name="revoked_reason",
        ),
        CheckConstraint("char_length(user_agent) <= 512", name="user_agent_length"),
    )
