import uuid
from typing import Protocol

from .pricing import PricingRecord, PricingSnapshot
from .queries import RecordQuery, SnapshotQuery, SnapshotSummary


class PricingSnapshotRepository(Protocol):
    """Snapshots and their records are append-only, and always reached through their
    organization: a snapshot of another organization is indistinguishable from a missing one."""

    async def add(self, snapshot: PricingSnapshot) -> None: ...

    async def get(self, organization_id: uuid.UUID, snapshot_id: uuid.UUID) -> PricingSnapshot | None:
        """With every record (for calculations)."""
        ...

    async def get_summary(
        self, organization_id: uuid.UUID, snapshot_id: uuid.UUID
    ) -> SnapshotSummary | None: ...

    async def list_for_organization(
        self, organization_id: uuid.UUID, query: SnapshotQuery
    ) -> list[SnapshotSummary]:
        """Newest first, at most ``query.limit``."""
        ...

    async def list_records(
        self, organization_id: uuid.UUID, snapshot_id: uuid.UUID, query: RecordQuery
    ) -> list[PricingRecord]:
        """By record id after ``query.after``, filtered, at most ``query.limit``."""
        ...
