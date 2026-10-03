import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .diffs import ArchitectureDiff
from .explanations import ExplanationRun
from .references import ComparedState


@dataclass(frozen=True, slots=True)
class DiffListing:
    """A diff in a project's list: what it compared and how much changed, without its parts."""

    id: uuid.UUID
    base: ComparedState
    target: ComparedState
    change_count: int
    counts: dict[str, Any]  # changes by kind and class: counts only
    explanations: int  # explanation runs stored for it
    requested_by_user_id: uuid.UUID
    compared_at: datetime


class ArchitectureDiffRepository(Protocol):
    """Diffs and their explanation runs belong to one project; every read is scoped by it. Both are
    append-only: a diff never changes, and asking for another explanation adds a run."""

    async def add(self, diff: ArchitectureDiff) -> ArchitectureDiff: ...

    async def get(self, project_id: uuid.UUID, diff_id: uuid.UUID) -> ArchitectureDiff | None: ...

    async def add_explanation(self, project_id: uuid.UUID, run: ExplanationRun) -> ExplanationRun: ...

    async def list_explanations(self, project_id: uuid.UUID, diff_id: uuid.UUID) -> list[ExplanationRun]:
        """Oldest first: the latest is the last."""
        ...

    async def list(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,  # either side a revision of it
        after: tuple[datetime, uuid.UUID] | None = None,  # the last (compared_at, id) of the previous page
        limit: int = 50,
    ) -> list[DiffListing]:
        """The project's diffs, newest first."""
        ...
