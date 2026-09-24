from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamps, UuidPrimaryKey


class OrganizationRecord(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Soft delete: the tenant and its audit history are retained; every tenant query filters it out.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name_length"),)
