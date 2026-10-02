import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .runs import AgentRun
from .values import FailureCode, RunStatus, Stage


@dataclass(frozen=True, slots=True)
class RunListing:
    """A run in a project's list: what it is and where it stands, without its parts."""

    id: uuid.UUID
    requirement_set_id: uuid.UUID
    base_architecture_id: uuid.UUID | None
    base_revision_number: int | None
    status: RunStatus
    stage: Stage
    model: str | None
    failure: FailureCode | None
    candidate_content_hash: str | None
    accepted_architecture_id: uuid.UUID | None
    accepted_revision_number: int | None
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    completed_at: datetime | None


class AgentRunRepository(Protocol):
    """Agent runs belong to one project; every read is scoped by it. A run's request never changes;
    a finished run (failed, cancelled, accepted, rejected) never changes again."""

    async def add(self, run: AgentRun) -> AgentRun: ...

    async def save(self, run: AgentRun) -> AgentRun:
        """The run's new state (the database refuses a change to its request or a finished run)."""
        ...

    async def get(
        self, project_id: uuid.UUID, run_id: uuid.UUID, *, for_update: bool = False
    ) -> AgentRun | None:
        """``for_update`` locks its row."""
        ...

    async def list(
        self,
        project_id: uuid.UUID,
        *,
        status: RunStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,  # the last (requested_at, id) of the previous page
        limit: int = 50,
    ) -> list[RunListing]:
        """The project's runs, newest first."""
        ...
