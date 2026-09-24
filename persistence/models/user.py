from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.identity.enums import UserStatus

from ._checks import in_values
from .base import Base, Timestamps, UuidPrimaryKey


class UserRecord(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    # Stored normalized (trimmed, lower-cased) by the domain; see core/domain/identity.
    email: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=UserStatus.ACTIVE.value)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Case-insensitive uniqueness enforced by the database itself, independent of the
        # application's normalization. Lookups use lower(email) so they hit this index.
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
        CheckConstraint("email = btrim(email) AND char_length(email) BETWEEN 3 AND 320", name="email_format"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 80", name="name_length"),
        CheckConstraint(in_values("status", UserStatus), name="status"),
        CheckConstraint("(status = 'deleted') = (deleted_at IS NOT NULL)", name="deleted_state"),
    )
