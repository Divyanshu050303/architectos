"""Listing an organization's pricing snapshots and a snapshot's records."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from core.domain import pagination

from .pricing import PricingSnapshot

_SNAPSHOTS = "pricing_snapshots"
_RECORDS = "pricing_records"


@dataclass(frozen=True, slots=True)
class SnapshotSummary:
    """A snapshot without its records."""

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None
    record_count: int
    content_hash: str
    created_at: datetime
    created_by_user_id: uuid.UUID | None

    @classmethod
    def of(cls, snapshot: PricingSnapshot) -> SnapshotSummary:
        return cls(
            snapshot.id,
            snapshot.organization_id,
            snapshot.name,
            snapshot.description,
            len(snapshot.records),
            snapshot.content_hash,
            snapshot.created_at,
            snapshot.created_by_user_id,
        )


@dataclass(frozen=True, slots=True)
class SnapshotQuery:
    after: tuple[datetime, uuid.UUID] | None = None  # newest first
    limit: int = 50


@dataclass(frozen=True, slots=True)
class RecordQuery:
    provider: str | None = None
    service: str | None = None
    region: str | None = None
    after: str | None = None  # the last record id of the previous page
    limit: int = 100


def encode_snapshot_cursor(summary: SnapshotSummary) -> str:
    return pagination.encode_cursor([_SNAPSHOTS, summary.created_at.isoformat(), str(summary.id)])


def decode_snapshot_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    kind, created_at, snapshot_id = pagination.decode_cursor(raw, length=3)
    if kind != _SNAPSHOTS:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(created_at), uuid.UUID(snapshot_id)
    except ValueError:
        raise pagination.InvalidCursor from None


def decode_record_cursor(raw: str) -> str:
    kind, record_id = pagination.decode_cursor(raw, length=2)
    if kind != _RECORDS or not record_id:
        raise pagination.InvalidCursor
    return record_id


def encode_record_cursor(record_id: str) -> str:
    return pagination.encode_cursor([_RECORDS, record_id])
