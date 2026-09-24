import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAt, UuidPrimaryKey


class SingleUseTokenRecord(UuidPrimaryKey, CreatedAt, Base):
    """Columns shared by the emailed single-use token tables. Only SHA-256 of the emailed value is
    stored. Not a table itself: each subclass declares its table name, indexes and checks."""

    __abstract__ = True

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when a newer token is issued for the same user, so only the latest link works.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
