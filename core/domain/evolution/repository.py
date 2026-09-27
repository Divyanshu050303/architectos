import uuid
from typing import Protocol

from .candidates import Candidate
from .queries import CandidateQuery, EvolutionQuery
from .reports import EvolutionReport


class EvolutionRepository(Protocol):
    """Evolution analyses and their candidates are append-only: stored once, finished, never
    changed. Always reached through their project and architecture."""

    async def add(self, report: EvolutionReport, candidates: tuple[Candidate, ...]) -> EvolutionReport:
        """``candidates`` in their canonical order: their position is their page order."""
        ...

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> EvolutionReport | None: ...

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: EvolutionQuery
    ) -> list[EvolutionReport]:
        """Newest first, at most ``query.limit``."""
        ...

    async def list_candidates(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: CandidateQuery
    ) -> list[tuple[int, Candidate]]:
        """With their positions, after ``query.after``, at most ``query.limit``."""
        ...

    async def get_candidate(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, candidate_id: str
    ) -> Candidate | None: ...

    async def candidates(self, project_id: uuid.UUID, analysis_id: uuid.UUID) -> tuple[Candidate, ...]:
        """Every candidate of an analysis, in order (to rebuild its result, e.g. to draft a decision)."""
        ...
