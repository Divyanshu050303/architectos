"""Project use cases. Authorization is part of each operation: the caller's membership is resolved
inside the operation's own transaction (for writes, with the project row locked), then the
permission is checked, then the change is made and audited in the same transaction."""

import uuid
from collections.abc import Callable
from datetime import datetime

from core.domain import pagination
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.errors import NothingToUpdate
from core.domain.organizations.entities import Membership
from core.domain.organizations.permissions import Permission
from core.domain.unit_of_work import UnitOfWork

from .entities import NewProject, Project, ProjectAccess
from .errors import ProjectNotFound
from .policies import ArchitecturePolicy
from .queries import ProjectCursor, ProjectQuery, ProjectSort
from .repository import ProjectLock
from .value_objects import ProjectSettings


def _cursor_for(project: Project, sort: ProjectSort) -> ProjectCursor:
    match sort:
        case ProjectSort.CREATED_AT:
            value = project.created_at.isoformat()
        case ProjectSort.UPDATED_AT:
            value = project.updated_at.isoformat()
        case ProjectSort.NAME:
            value = project.name.lower()
    return ProjectCursor(sort=sort, value=value, id=project.id)


def _restore(project: Project, _: datetime) -> Project:
    return project.restore()


class ProjectService:
    def __init__(self, uow: UnitOfWork, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._clock = clock

    async def resolve(self, *, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectAccess:
        async with self._uow as uow:
            access = await uow.projects.get_for_member(project_id, user_id=user_id)
        if access is None:
            raise ProjectNotFound
        access.membership.require(Permission.PROJECT_READ)
        return access

    async def list(self, *, membership: Membership, query: ProjectQuery) -> pagination.Page[Project]:
        membership.require(Permission.PROJECT_READ)
        size = pagination.page_size(query.limit)
        async with self._uow as uow:
            rows = await uow.projects.list_for_organization(
                membership.organization_id,
                ProjectQuery(query.status, query.search, query.sort, query.after, size + 1),
            )
        items, more = rows[:size], len(rows) > size
        next_cursor = _cursor_for(items[-1], query.sort).encode() if more and items else None
        return pagination.Page(items=items, next_cursor=next_cursor)

    async def create(
        self,
        *,
        membership: Membership,
        name: str,
        slug: str | None = None,
        description: str = "",
        settings: ProjectSettings | None = None,
    ) -> Project:
        membership.require(Permission.PROJECT_CREATE)
        new_project = NewProject.create(
            organization_id=membership.organization_id,
            created_by_user_id=membership.user_id,
            name=name,
            slug=slug,
            description=description,
            settings=settings,
        )
        async with self._uow as uow:
            # The creator's membership is re-checked inside the transaction that writes.
            current = await uow.memberships.get_in_active_organization(
                organization_id=membership.organization_id, user_id=membership.user_id
            )
            if current is None:
                raise ProjectNotFound
            current.membership.require(Permission.PROJECT_CREATE)
            project = await uow.projects.add(new_project)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.PROJECT_CREATED,
                    actor_user_id=membership.user_id,
                    organization_id=project.organization_id,
                    resource_type="project",
                    resource_id=project.id,
                    metadata={"name": project.name, "slug": project.slug},
                )
            )
        return project

    async def update(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        name: str | None = None,
        description: str | None = None,
        settings: ProjectSettings | None = None,
    ) -> ProjectAccess:
        if name is None and description is None and settings is None:
            raise NothingToUpdate
        async with self._uow as uow:
            access = await uow.projects.get_for_member(
                project_id, user_id=user_id, lock=ProjectLock.EXCLUSIVE
            )
            if access is None:
                raise ProjectNotFound
            access.membership.require(Permission.PROJECT_UPDATE)
            changed = access.project.with_changes(name=name, description=description, settings=settings)
            saved = await uow.projects.save(changed)
            fields = [
                field
                for field, supplied in (("name", name), ("description", description), ("settings", settings))
                if supplied is not None
            ]
            await uow.audit.record(
                AuditEvent(
                    AuditAction.PROJECT_UPDATED,
                    actor_user_id=user_id,
                    organization_id=saved.organization_id,
                    resource_type="project",
                    resource_id=saved.id,
                    metadata={"fields": fields},
                )
            )
        return ProjectAccess(project=saved, membership=access.membership)

    async def update_policy(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, policy: ArchitecturePolicy
    ) -> ProjectAccess:
        """Replaces the architecture policy (owners and admins: it constrains what members may
        design). Unchanged: nothing is saved or recorded. The audit names the changed fields,
        never their values."""
        async with self._uow as uow:
            access = await uow.projects.get_for_member(
                project_id, user_id=user_id, lock=ProjectLock.EXCLUSIVE
            )
            if access is None:
                raise ProjectNotFound
            access.membership.require(Permission.PROJECT_POLICY_UPDATE)
            current = access.project
            changed = current.with_policy(policy)
            if changed == current:
                return access
            saved = await uow.projects.save(changed)
            before, after = current.policy.to_dict(), policy.to_dict()
            await uow.audit.record(
                AuditEvent(
                    AuditAction.PROJECT_POLICY_UPDATED,
                    actor_user_id=user_id,
                    organization_id=saved.organization_id,
                    resource_type="project",
                    resource_id=saved.id,
                    metadata={"fields": sorted(k for k in after if after[k] != before[k])},
                )
            )
        return ProjectAccess(project=saved, membership=access.membership)

    # --- lifecycle ---------------------------------------------------------------------------

    async def archive(self, *, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectAccess:
        """Idempotent: archiving an archived project changes nothing and records nothing."""
        return await self._transition(
            project_id, user_id, Permission.PROJECT_ARCHIVE, AuditAction.PROJECT_ARCHIVED, Project.archive
        )

    async def restore(self, *, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectAccess:
        """Idempotent: restoring an active project changes nothing and records nothing."""
        return await self._transition(
            project_id, user_id, Permission.PROJECT_ARCHIVE, AuditAction.PROJECT_RESTORED, _restore
        )

    async def delete(self, *, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Soft delete, only from the archived state. Afterwards the project is not found anywhere and
        its slug can be reused; nothing is purged."""
        await self._transition(
            project_id, user_id, Permission.PROJECT_DELETE, AuditAction.PROJECT_DELETED, Project.delete
        )

    async def _transition(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        permission: Permission,
        action: AuditAction,
        step: Callable[[Project, datetime], Project],
    ) -> ProjectAccess:
        """Lock, re-authorize, apply one lifecycle step; save and audit only if the state changed."""
        now = self._clock()
        async with self._uow as uow:
            access = await uow.projects.get_for_member(
                project_id, user_id=user_id, lock=ProjectLock.EXCLUSIVE
            )
            if access is None:
                raise ProjectNotFound
            access.membership.require(permission)
            current = access.project
            target = step(current, now)
            if target == current:
                return access
            saved = await uow.projects.save(target)
            await uow.audit.record(
                AuditEvent(
                    action,
                    actor_user_id=user_id,
                    organization_id=saved.organization_id,
                    resource_type="project",
                    resource_id=saved.id,
                    metadata={"name": saved.name},
                )
            )
        return ProjectAccess(project=saved, membership=access.membership)
