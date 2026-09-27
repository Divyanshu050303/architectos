import uuid
from typing import Protocol

from .queries import SecurityAnalysisQuery, SecurityComponentQuery, SecurityFindingQuery
from .reports import SecurityReport
from .results import ComponentResult, SecurityFinding


class SecurityAnalysisRepository(Protocol):
    """Analyses, their components and findings are append-only: stored once, finished, never
    changed. Always reached through their project and architecture."""

    async def add(
        self,
        report: SecurityReport,
        components: tuple[ComponentResult, ...],
        findings: tuple[SecurityFinding, ...],
    ) -> SecurityReport:
        """``findings`` in their canonical order: their position is their page order."""
        ...

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> SecurityReport | None: ...

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: SecurityAnalysisQuery
    ) -> list[SecurityReport]:
        """Newest first, at most ``query.limit``."""
        ...

    async def list_components(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: SecurityComponentQuery
    ) -> list[ComponentResult]:
        """By node id after ``query.after``, at most ``query.limit``."""
        ...

    async def list_findings(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: SecurityFindingQuery
    ) -> list[tuple[int, SecurityFinding]]:
        """With their positions, after ``query.after``, at most ``query.limit``."""
        ...
