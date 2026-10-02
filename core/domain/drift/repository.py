import uuid
from datetime import datetime
from typing import Protocol

from .analyses import DriftAnalysis
from .identity import IdentityMapping
from .items import DriftItem
from .values import AnalysisStatus, ReviewStatus


class DriftRepository(Protocol):
    """Drift analyses (append-only), drift items (identity fixed; review status, history, links and the
    latest detection change) and identity mappings (append-only) — every read scoped by project."""

    async def add_analysis(self, analysis: DriftAnalysis) -> DriftAnalysis: ...

    async def get_analysis(self, project_id: uuid.UUID, analysis_id: uuid.UUID) -> DriftAnalysis | None: ...

    async def list_analyses(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: AnalysisStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,  # the last (requested_at, id) of the previous page
        limit: int = 50,
    ) -> list[DriftAnalysis]:
        """Newest first."""
        ...

    async def uses_discovery_run(self, project_id: uuid.UUID, run_id: uuid.UUID) -> bool:
        """Whether any analysis compared that discovery run (it is then kept)."""
        ...

    async def add_items(self, items: tuple[DriftItem, ...]) -> None: ...

    async def save_item(self, item: DriftItem) -> DriftItem:
        """The item's review status, history, links, artifacts and latest detection."""
        ...

    async def get_item(
        self, project_id: uuid.UUID, item_id: uuid.UUID, *, for_update: bool = False
    ) -> DriftItem | None: ...

    async def items_of(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, *, for_update: bool = False
    ) -> list[DriftItem]:
        """Every item of an architecture (locked for correlation)."""
        ...

    async def list_items(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: ReviewStatus | None = None,
        after: uuid.UUID | None = None,  # the last id of the previous page
        limit: int = 50,
    ) -> list[DriftItem]:
        """In creation order (ids are time-ordered)."""
        ...

    async def add_mapping(self, project_id: uuid.UUID, mapping: IdentityMapping) -> IdentityMapping: ...

    async def mappings(self, project_id: uuid.UUID, architecture_id: uuid.UUID) -> list[IdentityMapping]:
        """Every mapping of the architecture, oldest first (history: the latest per node applies)."""
        ...
