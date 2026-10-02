import uuid
from datetime import datetime
from typing import Protocol

from .runs import DiscoveryRun, RunListing
from .values import RunStatus


class DiscoveryRunRepository(Protocol):
    """Discovery runs belong to one project; every read is scoped by it. A run's request and result are
    written once; only its review decisions and acceptances change afterwards."""

    async def add(self, run: DiscoveryRun) -> DiscoveryRun: ...

    async def update_review(self, run: DiscoveryRun) -> DiscoveryRun:
        """The run's decisions and acceptances (nothing else changes)."""
        ...

    async def get(
        self, project_id: uuid.UUID, run_id: uuid.UUID, *, for_update: bool = False
    ) -> DiscoveryRun | None:
        """The run with its result; ``for_update`` locks its row."""
        ...

    async def list(
        self,
        project_id: uuid.UUID,
        *,
        status: RunStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,  # the last (requested_at, id) of the previous page
        limit: int = 50,
    ) -> list[RunListing]:
        """The project's runs, newest first, without their results."""
        ...

    async def accepted_for(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID
    ) -> tuple[DiscoveryRun, ...]:
        """The runs whose proposal was accepted into the architecture (oldest first)."""
        ...

    async def delete(self, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        """Remove a run that no acceptance refers to (the caller checked)."""
        ...
