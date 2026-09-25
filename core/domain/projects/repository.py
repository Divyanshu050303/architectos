import uuid
from typing import Protocol

from .entities import NewProject, Project, ProjectAccess
from .queries import ProjectQuery


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
        self, project_id: uuid.UUID, *, user_id: uuid.UUID, for_update: bool = False
    ) -> ProjectAccess | None:
        """The tenant entry point: the project, found only if it is not deleted, its organization
        is not deleted and ``user_id`` is a member of that organization (one query). With
        ``for_update`` the project row is locked until the transaction ends."""
        ...

    async def list_for_organization(self, organization_id: uuid.UUID, query: ProjectQuery) -> list[Project]:
        """Live projects of one organization, filtered and ordered in the database, at most
        ``query.limit`` rows after ``query.after``."""
        ...
