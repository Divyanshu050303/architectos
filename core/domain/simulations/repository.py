import uuid
from typing import Protocol

from .queries import SimulationComponentQuery, SimulationDeltaQuery, SimulationQuery
from .reports import SimulationReport
from .results import ComponentOutcome, Delta


class SimulationRepository(Protocol):
    """Simulations, their component outcomes and deltas are append-only: stored once, finished,
    never changed. Always reached through their project and architecture."""

    async def add(
        self,
        report: SimulationReport,
        components: tuple[ComponentOutcome, ...],
        deltas: tuple[Delta, ...],
    ) -> SimulationReport:
        """``deltas`` in their canonical order: their position is their page order."""
        ...

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, simulation_id: uuid.UUID
    ) -> SimulationReport | None: ...

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: SimulationQuery
    ) -> list[SimulationReport]:
        """Newest first, at most ``query.limit``."""
        ...

    async def list_components(
        self, project_id: uuid.UUID, simulation_id: uuid.UUID, query: SimulationComponentQuery
    ) -> list[ComponentOutcome]:
        """By node id after ``query.after``, at most ``query.limit``."""
        ...

    async def list_deltas(
        self, project_id: uuid.UUID, simulation_id: uuid.UUID, query: SimulationDeltaQuery
    ) -> list[tuple[int, Delta]]:
        """With their positions, after ``query.after``, at most ``query.limit``."""
        ...

    async def rows(
        self, project_id: uuid.UUID, simulation_id: uuid.UUID
    ) -> tuple[tuple[ComponentOutcome, ...], tuple[Delta, ...]]:
        """Every component outcome and delta of a simulation (to rebuild its result, e.g. to compare)."""
        ...
