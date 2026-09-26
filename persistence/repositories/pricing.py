"""Pricing snapshots and records (append-only), always read through their organization."""

import uuid

from sqlalchemy import insert, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.cost.pricing import PricingRecord, PricingSnapshot
from core.domain.cost.queries import RecordQuery, SnapshotQuery, SnapshotSummary
from persistence.models import PricingRecordRow, PricingSnapshotRecord

_BATCH = 500


def _summary(record: PricingSnapshotRecord) -> SnapshotSummary:
    return SnapshotSummary(
        record.id,
        record.organization_id,
        record.name,
        record.description,
        record.record_count,
        record.content_hash,
        record.created_at,
        record.created_by_user_id,
    )


class SqlAlchemyPricingSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, snapshot: PricingSnapshot) -> None:
        self._session.add(
            PricingSnapshotRecord(
                id=snapshot.id,
                organization_id=snapshot.organization_id,
                name=snapshot.name,
                description=snapshot.description,
                content_hash=snapshot.content_hash,
                record_count=len(snapshot.records),
                created_by_user_id=snapshot.created_by_user_id,
                created_at=snapshot.created_at,
            )
        )
        await self._session.flush()
        rows = [
            {
                "id": uuid.uuid7(),
                "snapshot_id": snapshot.id,
                "organization_id": snapshot.organization_id,
                "record_id": r.id,
                "provider": r.provider,
                "service": r.service,
                "sku": r.sku,
                "region": r.region,
                "currency": r.currency,
                "effective_from": r.effective_from,
                "data": r.to_dict(),
            }
            for r in snapshot.records
        ]
        for start in range(0, len(rows), _BATCH):
            await self._session.execute(insert(PricingRecordRow), rows[start : start + _BATCH])

    async def _record(
        self, organization_id: uuid.UUID, snapshot_id: uuid.UUID
    ) -> PricingSnapshotRecord | None:
        record: PricingSnapshotRecord | None = await self._session.scalar(
            select(PricingSnapshotRecord).where(
                PricingSnapshotRecord.id == snapshot_id,
                PricingSnapshotRecord.organization_id == organization_id,
            )
        )
        return record

    async def get(self, organization_id: uuid.UUID, snapshot_id: uuid.UUID) -> PricingSnapshot | None:
        record = await self._record(organization_id, snapshot_id)
        if record is None:
            return None
        rows = await self._session.scalars(
            select(PricingRecordRow.data)
            .where(
                PricingRecordRow.snapshot_id == snapshot_id,
                PricingRecordRow.organization_id == organization_id,
            )
            .order_by(PricingRecordRow.record_id)
        )
        return PricingSnapshot(
            id=record.id,
            organization_id=record.organization_id,
            name=record.name,
            records=tuple(PricingRecord.from_dict(data) for data in rows),
            created_at=record.created_at,
            created_by_user_id=record.created_by_user_id,
            description=record.description,
        )

    async def get_summary(self, organization_id: uuid.UUID, snapshot_id: uuid.UUID) -> SnapshotSummary | None:
        record = await self._record(organization_id, snapshot_id)
        return _summary(record) if record else None

    async def list_for_organization(
        self, organization_id: uuid.UUID, query: SnapshotQuery
    ) -> list[SnapshotSummary]:
        statement = select(PricingSnapshotRecord).where(
            PricingSnapshotRecord.organization_id == organization_id
        )
        if query.after is not None:
            created_at, snapshot_id = query.after
            statement = statement.where(
                tuple_(PricingSnapshotRecord.created_at, PricingSnapshotRecord.id)
                < tuple_(literal(created_at), literal(snapshot_id))
            )
        ordered = statement.order_by(PricingSnapshotRecord.created_at.desc(), PricingSnapshotRecord.id.desc())
        return [_summary(r) for r in await self._session.scalars(ordered.limit(query.limit))]

    async def list_records(
        self, organization_id: uuid.UUID, snapshot_id: uuid.UUID, query: RecordQuery
    ) -> list[PricingRecord]:
        statement = select(PricingRecordRow.data).where(
            PricingRecordRow.snapshot_id == snapshot_id, PricingRecordRow.organization_id == organization_id
        )
        for column, value in (
            (PricingRecordRow.provider, query.provider),
            (PricingRecordRow.service, query.service),
            (PricingRecordRow.region, query.region),
        ):
            if value is not None:
                statement = statement.where(column == value)
        if query.after is not None:
            statement = statement.where(PricingRecordRow.record_id > query.after)
        rows = await self._session.scalars(statement.order_by(PricingRecordRow.record_id).limit(query.limit))
        return [PricingRecord.from_dict(data) for data in rows]
