import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from core.domain.organizations.enums import Role

from ._checks import in_values
from .base import Base, Timestamps, UuidPrimaryKey


class OrganizationMemberRecord(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "organization_members"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        # One membership per user per organization. Its leading column also serves
        # "members of organization X", so no separate organization_id index is needed.
        UniqueConstraint("organization_id", "user_id"),
        # "Organizations of user Y" (the organization switcher, membership resolution on login).
        Index(None, "user_id"),
        CheckConstraint(in_values("role", Role), name="role"),
    )
