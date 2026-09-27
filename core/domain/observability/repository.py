import uuid
from typing import Protocol

from .queries import ObservabilityAnalysisQuery, ObservabilityComponentQuery, ObservabilityFindingQuery
from .reports import ObservabilityReport
from .results import ComponentResult, ObservabilityFinding


class ObservabilityAnalysisRepository(Protocol):
    """Analyses, their components and findings are append-only: stored once, finished, never
    changed. Always reached through their project and architecture."""

    async def add(
        self,
        report: ObservabilityReport,
        components: tuple[ComponentResult, ...],
        findings: tuple[ObservabilityFinding, ...],
    ) -> ObservabilityReport:
        """``findings`` in their canonical order: their position is their page order."""
        ...

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> ObservabilityReport | None: ...

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: ObservabilityAnalysisQuery
    ) -> list[ObservabilityReport]:
        """Newest first, at most ``query.limit``."""
        ...

    async def list_components(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ObservabilityComponentQuery
    ) -> list[ComponentResult]:
        """By node id after ``query.after``, at most ``query.limit``."""
        ...

    async def list_findings(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ObservabilityFindingQuery
    ) -> list[tuple[int, ObservabilityFinding]]:
        """With their positions, after ``query.after``, at most ``query.limit``."""
        ...
