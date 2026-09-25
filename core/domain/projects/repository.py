import uuid
from enum import StrEnum
from typing import Protocol

from .entities import NewProject, Project, ProjectAccess
from .queries import ProjectQuery


class ProjectLock(StrEnum):
    """How a write holds its project row until the transaction ends.

    SHARE: for changes to one thing under the project (editing or deleting a requirement). Many
    such writes run in parallel, but none while the project itself changes (archive, update).
    EXCLUSIVE: for changes to the project, and for writes that must see a stable project-wide
    state (allocating a requirement number, snapshotting a requirement set)."""

    SHARE = "share"
    EXCLUSIVE = "exclusive"


class ProjectRepository(Protocol):
    async def add(self, project: NewProject) -> Project:
        """Raises ProjectSlugTaken if the organization already has a live project with this slug;
        the surrounding transaction stays usable."""
        ...

    async def get_live(self, project_id: uuid.UUID) -> Project | None:
        """A project that is not deleted, regardless of organization. Callers must scope it: API code
        resolves projects through the caller's membership, never through this alone."""
        ...

    async def get_live_for_update(self, project_id: uuid.UUID) -> Project | None:
        """Like get_live, locking the row until the transaction ends (serializes lifecycle changes)."""
        ...

    async def save(self, project: Project) -> Project:
        """Persists name, description, settings, status and lifecycle timestamps (never
        organization, slug or creator) and returns the stored state."""
        ...

    async def get_for_member(
        self, project_id: uuid.UUID, *, user_id: uuid.UUID, lock: ProjectLock | None = None
    ) -> ProjectAccess | None:
        """The tenant entry point: the project, found only if it is not deleted, its organization
        is not deleted and ``user_id`` is a member of that organization (one query). With ``lock`` the
        project row is locked (see ProjectLock) until the transaction ends."""
        ...

    async def list_for_organization(self, organization_id: uuid.UUID, query: ProjectQuery) -> list[Project]:
        """Live projects of one organization, filtered and ordered in the database, at most
        ``query.limit`` rows after ``query.after``."""
        ...
