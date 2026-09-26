"""Listing an organization's pricing snapshots and a snapshot's records."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from core.domain import pagination

from .pricing import PricingSnapshot
from .results import CostCategory, LineStatus

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


_COST_ANALYSES = "cost_analyses"
_LINE_ITEMS = "cost_line_items"


@dataclass(frozen=True, slots=True)
class CostAnalysisQuery:
    """An architecture's cost analyses, newest first."""

    revision: int | None = None
    after: tuple[datetime, uuid.UUID] | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class LineItemQuery:
    """An analysis's line items by component and resource."""

    element_id: str | None = None
    status: LineStatus | None = None
    category: CostCategory | None = None
    after: tuple[str, str] | None = None  # the last (component, resource) of the previous page
    limit: int = 100


def encode_cost_analysis_cursor(requested_at: datetime, analysis_id: uuid.UUID) -> str:
    return pagination.encode_cursor([_COST_ANALYSES, requested_at.isoformat(), str(analysis_id)])


def decode_cost_analysis_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    kind, requested_at, analysis_id = pagination.decode_cursor(raw, length=3)
    if kind != _COST_ANALYSES:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(requested_at), uuid.UUID(analysis_id)
    except ValueError:
        raise pagination.InvalidCursor from None


def encode_line_item_cursor(element_id: str, resource: str) -> str:
    return pagination.encode_cursor([_LINE_ITEMS, element_id, resource])


def decode_line_item_cursor(raw: str) -> tuple[str, str]:
    kind, element_id, resource = pagination.decode_cursor(raw, length=3)
    if kind != _LINE_ITEMS or not element_id or not resource:
        raise pagination.InvalidCursor
    return element_id, resource
