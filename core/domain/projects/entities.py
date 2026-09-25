"""The Project aggregate. Immutable: every change returns a new Project, so a service can only
persist what the domain allowed.

Rules (see docs/domain/projects.md):
- a project belongs to exactly one organization, fixed at creation (there is no way to change it);
- archived projects are read-only: only restore (or delete) is allowed;
- archive and restore are idempotent;
- deletion is a soft delete and requires the archived state;
- the slug never changes after creation.
"""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime

from .enums import ProjectStatus
from .errors import ProjectArchived, ProjectNotArchived, ProjectNotFound
from .value_objects import (
    ProjectSettings,
    normalize_project_description,
    normalize_project_name,
    normalize_slug,
    slugify,
)


@dataclass(frozen=True, slots=True)
class NewProject:
    """A validated creation request, before it has an id or timestamps."""

    organization_id: uuid.UUID
    name: str
    slug: str
    description: str
    settings: ProjectSettings
    created_by_user_id: uuid.UUID

    @classmethod
    def create(
        cls,
        *,
        organization_id: uuid.UUID,
        created_by_user_id: uuid.UUID,
        name: str,
        slug: str | None = None,
        description: str = "",
        settings: ProjectSettings | None = None,
    ) -> NewProject:
        clean_name = normalize_project_name(name)
        return cls(
            organization_id=organization_id,
            name=clean_name,
            slug=normalize_slug(slug) if slug is not None else slugify(clean_name),
            description=normalize_project_description(description),
            settings=settings or ProjectSettings(),
            created_by_user_id=created_by_user_id,
        )


@dataclass(frozen=True, slots=True)
class Project:
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    slug: str
    description: str
    status: ProjectStatus
    settings: ProjectSettings
    created_by_user_id: uuid.UUID | None
    archived_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def is_archived(self) -> bool:
        return self.status is ProjectStatus.ARCHIVED

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def ensure_modifiable(self) -> None:
        if self.is_deleted:
            raise ProjectNotFound
        if self.is_archived:
            raise ProjectArchived

    def with_changes(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        settings: ProjectSettings | None = None,
    ) -> Project:
        """The only way to edit a project: name, description, settings. Organization, slug,
        creator and timestamps are not parameters, so they cannot change here."""
        self.ensure_modifiable()
        return replace(
            self,
            name=normalize_project_name(name) if name is not None else self.name,
            description=normalize_project_description(description)
            if description is not None
            else self.description,
            settings=settings if settings is not None else self.settings,
        )

    def archive(self, at: datetime) -> Project:
        if self.is_deleted:
            raise ProjectNotFound
        if self.is_archived:
            return self  # idempotent: the original archive time is kept
        return replace(self, status=ProjectStatus.ARCHIVED, archived_at=at)

    def restore(self) -> Project:
        if self.is_deleted:
            raise ProjectNotFound
        if not self.is_archived:
            return self  # idempotent
        return replace(self, status=ProjectStatus.ACTIVE, archived_at=None)

    def delete(self, at: datetime) -> Project:
        if self.is_deleted:
            raise ProjectNotFound
        if not self.is_archived:
            raise ProjectNotArchived
        return replace(self, deleted_at=at)
