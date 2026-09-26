import uuid
from collections.abc import Mapping
from typing import Protocol

from .entities import (
    Architecture,
    ArchitectureLayout,
    ArchitectureQuery,
    NewArchitecture,
    Position,
    RevisionSummary,
)
from .versions import ArchitectureRevision, NewRevision


class ArchitectureRepository(Protocol):
    """Architectures are always reached through their project: ``get`` takes both ids, so an
    architecture of another project (or tenant) is indistinguishable from a missing one, and
    revisions and layouts are only ever read for an architecture already found that way.
    Revisions are append-only; deleted architectures are never returned."""

    async def add(
        self, architecture: NewArchitecture, first: NewRevision
    ) -> tuple[Architecture, ArchitectureRevision]:
        """Stores the architecture with its first revision. Raises ArchitectureNameTaken if a live
        architecture of the project has the same name (the transaction stays usable)."""
        ...

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, *, for_update: bool = False
    ) -> Architecture | None:
        """A live architecture of the project; ``for_update`` locks its row until the transaction
        ends (revisions and metadata changes are then serialized)."""
        ...

    async def list_for_project(self, project_id: uuid.UUID, query: ArchitectureQuery) -> list[Architecture]:
        """Live architectures, newest first, filtered in the database, at most ``query.limit``."""
        ...

    async def save(self, architecture: Architecture) -> Architecture:
        """Persists metadata and lifecycle changes (never the current revision, which only
        ``add_revision`` moves). Raises ArchitectureNameTaken on a duplicate live name."""
        ...

    async def add_revision(
        self, architecture: Architecture, revision: NewRevision
    ) -> tuple[Architecture, ArchitectureRevision]:
        """Appends ``revision`` (whose parent must be the current revision) and makes it current."""
        ...

    async def get_revision(self, architecture_id: uuid.UUID, number: int) -> ArchitectureRevision | None: ...

    async def list_revisions(
        self, architecture_id: uuid.UUID, *, before: int | None, limit: int
    ) -> list[RevisionSummary]:
        """Newest first, numbers below ``before``, at most ``limit``; never the content."""
        ...

    async def get_layout(self, architecture_id: uuid.UUID) -> ArchitectureLayout: ...

    async def save_layout(
        self, architecture: Architecture, positions: Mapping[str, Position], user_id: uuid.UUID
    ) -> ArchitectureLayout: ...
