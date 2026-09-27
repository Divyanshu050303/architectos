import uuid
from typing import Protocol

from .entities import Decision, DecisionStatus


class DecisionRepository(Protocol):
    """Decisions belong to one project; every read is scoped by it. A decision's lifecycle changes
    (accepted, rejected, superseded, linked) replace the record; each change is also audited."""

    async def add(self, decision: Decision) -> Decision: ...

    async def update(self, decision: Decision) -> Decision:
        """The record with its new status (the id and project never change)."""
        ...

    async def get(self, project_id: uuid.UUID, decision_id: uuid.UUID) -> Decision | None: ...

    async def next_number(self, project_id: uuid.UUID) -> int:
        """The next ADR number of the project (under the project's write lock)."""
        ...

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: DecisionStatus | None = None,
        after: int | None = None,  # the last number of the previous page
        limit: int = 50,
    ) -> list[Decision]:
        """By number, ascending."""
        ...
