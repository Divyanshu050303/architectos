import uuid
from collections.abc import Mapping
from typing import Protocol

from .entities import Architecture, ArchitectureLayout, NewArchitecture, Position, RevisionSummary
from .versions import ArchitectureRevision, NewRevision


class ArchitectureRepository(Protocol):
    """Every method is scoped by project: a project has at most one architecture, and nothing of
    another project (or tenant) can be reached through it. Revisions are append-only."""

    async def add(
        self, architecture: NewArchitecture, first: NewRevision
    ) -> tuple[Architecture, ArchitectureRevision]:
        """Stores the architecture with its first revision. Raises ArchitectureAlreadyExists if the
        project has one (the surrounding transaction stays usable)."""
        ...

    async def get(self, project_id: uuid.UUID, *, for_update: bool = False) -> Architecture | None:
        """``for_update`` locks the row until the transaction ends (revisions are then serialized)."""
        ...

    async def add_revision(
        self, architecture: Architecture, revision: NewRevision
    ) -> tuple[Architecture, ArchitectureRevision]:
        """Appends ``revision`` (whose parent must be the current revision) and makes it current."""
        ...

    async def get_revision(self, project_id: uuid.UUID, number: int) -> ArchitectureRevision | None: ...

    async def list_revisions(
        self, project_id: uuid.UUID, *, before: int | None, limit: int
    ) -> list[RevisionSummary]:
        """Newest first, numbers below ``before``, at most ``limit``."""
        ...

    async def get_layout(self, project_id: uuid.UUID) -> ArchitectureLayout: ...

    async def save_layout(
        self, architecture: Architecture, positions: Mapping[str, Position], user_id: uuid.UUID
    ) -> ArchitectureLayout: ...
