"""Pricing snapshots of an organization and their records. Both tables are append-only (a price
list never changes; a new one is a new snapshot), and records reach their snapshot through a
same-organization foreign key."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UuidPrimaryKey


class PricingSnapshotRecord(UuidPrimaryKey, Base):
    __tablename__ = "pricing_snapshots"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # No foreign key, like the other append-only tables: an immutable row cannot be SET NULL.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("id", "organization_id"),  # target of the records' foreign key
        # An organization's snapshots, newest first; also serves the RESTRICT check.
        Index(None, "organization_id", "created_at", "id"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name_length"),
        CheckConstraint("description IS NULL OR char_length(description) <= 500", name="description_length"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        CheckConstraint("record_count BETWEEN 1 AND 10000", name="record_count_range"),
    )


class PricingRecordRow(UuidPrimaryKey, Base):
    """One price of a snapshot; ``data`` is the whole record, the other columns copies for lookup."""

    __tablename__ = "pricing_records"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    record_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    service: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "record_id"),  # paging walks it; ids are unique per snapshot
        ForeignKeyConstraint(
            ["snapshot_id", "organization_id"],
            ["pricing_snapshots.id", "pricing_snapshots.organization_id"],
            name="fk_pricing_records_snapshot_pricing_snapshots",
            ondelete="RESTRICT",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )
