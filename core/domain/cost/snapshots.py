"""Pricing snapshots of an organization: create (immutable), list, read, and page through records.

Price lists belong to an organization, not to a project: prices (list prices, negotiated rates)
are the same for every project of the organization. Creating one needs ``pricing.manage`` (owners
and admins); reading needs ``organization.read``. A snapshot is never changed: a new price list is
a new snapshot, so an analysis that cited one can always be explained with exactly its prices.
Audit entries carry identifiers and counts, never prices.
"""

import uuid

from core.domain import pagination
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.organizations.entities import Membership
from core.domain.organizations.permissions import Permission
from core.domain.unit_of_work import UnitOfWork

from .errors import PricingSnapshotNotFound
from .pricing import PricingRecord, PricingSnapshot
from .queries import (
    RecordQuery,
    SnapshotQuery,
    SnapshotSummary,
    decode_record_cursor,
    decode_snapshot_cursor,
    encode_record_cursor,
    encode_snapshot_cursor,
)

MAX_RECORD_PAGE = 500


class PricingService:
    def __init__(self, uow: UnitOfWork, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._clock = clock

    async def create(
        self,
        *,
        membership: Membership,
        name: str,
        records: tuple[PricingRecord, ...],
        description: str | None = None,
    ) -> SnapshotSummary:
        membership.require(Permission.PRICING_MANAGE)
        snapshot = PricingSnapshot(
            id=uuid.uuid7(),
            organization_id=membership.organization_id,
            name=" ".join(name.split()) if isinstance(name, str) else name,
            records=records,
            created_at=self._clock(),
            created_by_user_id=membership.user_id,
            description=description,
        )
        async with self._uow as uow:
            current = await uow.memberships.get_in_active_organization(
                organization_id=membership.organization_id, user_id=membership.user_id
            )
            if current is None:
                raise PricingSnapshotNotFound  # the organization is gone for this caller
            current.membership.require(Permission.PRICING_MANAGE)
            await uow.pricing.add(snapshot)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.PRICING_SNAPSHOT_CREATED,
                    actor_user_id=membership.user_id,
                    organization_id=membership.organization_id,
                    resource_type="pricing_snapshot",
                    resource_id=snapshot.id,
                    metadata={"records": len(snapshot.records), "content_sha256": snapshot.content_hash},
                )
            )
        return SnapshotSummary.of(snapshot)

    async def list(
        self, *, membership: Membership, cursor: str | None = None, limit: int = 50
    ) -> pagination.Page[SnapshotSummary]:
        membership.require(Permission.ORGANIZATION_READ)
        size = pagination.page_size(limit)
        query = SnapshotQuery(decode_snapshot_cursor(cursor) if cursor else None, size + 1)
        async with self._uow as uow:
            rows = await uow.pricing.list_for_organization(membership.organization_id, query)
        items, more = rows[:size], len(rows) > size
        return pagination.Page(items=items, next_cursor=encode_snapshot_cursor(items[-1]) if more else None)

    async def get(self, *, membership: Membership, snapshot_id: uuid.UUID) -> SnapshotSummary:
        membership.require(Permission.ORGANIZATION_READ)
        async with self._uow as uow:
            summary = await uow.pricing.get_summary(membership.organization_id, snapshot_id)
        if summary is None:
            raise PricingSnapshotNotFound
        return summary

    async def records(
        self,
        *,
        membership: Membership,
        snapshot_id: uuid.UUID,
        query: RecordQuery,
        cursor: str | None = None,
    ) -> pagination.Page[PricingRecord]:
        membership.require(Permission.ORGANIZATION_READ)
        size = max(1, min(query.limit, MAX_RECORD_PAGE))
        after = decode_record_cursor(cursor) if cursor else None
        async with self._uow as uow:
            if await uow.pricing.get_summary(membership.organization_id, snapshot_id) is None:
                raise PricingSnapshotNotFound
            rows = await uow.pricing.list_records(
                membership.organization_id,
                snapshot_id,
                RecordQuery(query.provider, query.service, query.region, after, size + 1),
            )
        items, more = rows[:size], len(rows) > size
        next_cursor = encode_record_cursor(items[-1].id) if more and items else None
        return pagination.Page(items=items, next_cursor=next_cursor)
