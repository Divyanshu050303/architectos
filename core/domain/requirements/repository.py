import uuid
from typing import Protocol

from .entities import NewRequirement, Requirement, RequirementVersion, Revision
from .queries import RequirementQuery


class RequirementRepository(Protocol):
    """Every method is scoped by project: a requirement id alone never finds anything, so a
    requirement of another project (or tenant) is indistinguishable from a missing one.

    Writes assume the caller holds the project row lock (see Project.ensure_modifiable): that
    serializes number allocation and keeps archived projects frozen."""

    async def add(self, requirement: NewRequirement) -> Requirement:
        """Allocates the next number in the project, stores the requirement and its version 1."""
        ...

    async def get(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, *, for_update: bool = False
    ) -> Requirement | None:
        """A requirement of the project that is not deleted; ``for_update`` locks its row."""
        ...

    async def save(self, revision: Revision) -> Requirement:
        """Appends ``revision`` as version ``revision.requirement.version`` and makes it the current
        state; returns the stored requirement."""
        ...

    async def save_deleted(self, requirement: Requirement) -> None:
        """Persists the soft delete (``deleted_at``); versions are untouched."""
        ...

    async def list_for_project(self, project_id: uuid.UUID, query: RequirementQuery) -> list[Requirement]:
        """Live requirements, newest first, filtered in the database, at most ``query.limit`` rows."""
        ...

    async def list_versions(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, *, after: int | None, limit: int
    ) -> list[RequirementVersion]:
        """Versions of a live requirement in ascending order, after version ``after``."""
        ...

    async def get_version(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, version: int
    ) -> RequirementVersion | None:
        """One version of a live requirement."""
        ...
