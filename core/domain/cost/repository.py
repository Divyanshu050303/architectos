import uuid
from typing import Protocol

from .pricing import PricingRecord, PricingSnapshot
from .queries import CostAnalysisQuery, LineItemQuery, RecordQuery, SnapshotQuery, SnapshotSummary
from .reports import CostReport
from .results import LineItem


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


class CostAnalysisRepository(Protocol):
    """Cost analyses and their line items are append-only: stored once, finished, never changed.
    Always reached through their project and architecture."""

    async def add(self, report: CostReport, line_items: tuple[LineItem, ...]) -> CostReport: ...

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> CostReport | None: ...

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: CostAnalysisQuery
    ) -> list[CostReport]:
        """Newest first, at most ``query.limit``."""
        ...

    async def list_line_items(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: LineItemQuery
    ) -> list[LineItem]:
        """By (component, resource) after ``query.after``, at most ``query.limit``."""
        ...
