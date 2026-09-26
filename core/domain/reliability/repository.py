import uuid
from typing import Protocol

from .queries import ReliabilityAnalysisQuery, ReliabilityComponentQuery, ReliabilityFindingQuery
from .reports import ReliabilityReport
from .results import ComponentResult, ReliabilityFinding


class ReliabilityAnalysisRepository(Protocol):
    """Analyses, their components and findings are append-only: stored once, finished, never
    changed. Always reached through their project and architecture."""

    async def add(
        self,
        report: ReliabilityReport,
        components: tuple[ComponentResult, ...],
        findings: tuple[ReliabilityFinding, ...],
    ) -> ReliabilityReport:
        """``findings`` in their canonical order: their position is their page order."""
        ...

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> ReliabilityReport | None: ...

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: ReliabilityAnalysisQuery
    ) -> list[ReliabilityReport]:
        """Newest first, at most ``query.limit``."""
        ...

    async def list_components(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ReliabilityComponentQuery
    ) -> list[ComponentResult]:
        """By node id after ``query.after``, at most ``query.limit``."""
        ...

    async def list_findings(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ReliabilityFindingQuery
    ) -> list[tuple[int, ReliabilityFinding]]:
        """With their positions, after ``query.after``, at most ``query.limit``."""
        ...
