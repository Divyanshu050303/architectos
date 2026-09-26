import uuid
from typing import Protocol

from .queries import FindingQuery, RunQuery
from .results import Finding
from .runs import RunInputs, RunReport, ValidationRun


class ValidationRunRepository(Protocol):
    """Runs and their findings are append-only: stored once, completed or failed, never changed.
    Runs are always reached through their project and architecture, so a run of another
    architecture, project or tenant is indistinguishable from a missing one."""

    async def add(self, run: ValidationRun, inputs: RunInputs) -> RunReport:
        """Stores a finished run with its result (findings in canonical order) and inputs."""
        ...

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, run_id: uuid.UUID
    ) -> RunReport | None: ...

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: RunQuery
    ) -> list[RunReport]:
        """Newest first, at most ``query.limit``."""
        ...

    async def list_findings(
        self, project_id: uuid.UUID, run_id: uuid.UUID, query: FindingQuery
    ) -> list[tuple[int, Finding]]:
        """(position, finding) of a run of the project (scoped by both, as defense in depth), in
        canonical order after ``query.after``, at most ``query.limit``."""
        ...
