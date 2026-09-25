"""Requirement use cases. Each one resolves the project together with the caller's membership in
its own transaction (a requirement is only ever reached through its project), checks the
permission, and for writes locks the project row first: that serializes number allocation, and
makes archiving and requirement changes mutually exclusive, so an archived project is frozen."""

import uuid
from typing import Any

from core.domain import pagination
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.errors import ProjectNotFound
from core.domain.unit_of_work import UnitOfWork

from .analysis import ANALYZED_STATUSES, ProjectAnalysis, ValidationReport, analyze, validate_requirement
from .entities import NewRequirement, Requirement, RequirementChanges, Revision
from .enums import RequirementPriority, RequirementSource, RequirementStatus, RequirementType
from .errors import RequirementNotFound
from .queries import RequirementCursor, RequirementQuery

MAX_ANALYZED_REQUIREMENTS = 2000


class RequirementService:
    def __init__(self, uow: UnitOfWork, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._clock = clock

    async def list(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, query: RequirementQuery
    ) -> pagination.Page[Requirement]:
        size = pagination.page_size(query.limit)
        async with self._uow as uow:
            await self._access(uow, project_id, user_id, Permission.REQUIREMENT_READ)
            rows = await uow.requirements.list_for_project(
                project_id,
                RequirementQuery(
                    query.type,
                    query.category,
                    query.status,
                    query.priority,
                    query.search,
                    query.after,
                    size + 1,
                ),
            )
        items, more = rows[:size], len(rows) > size
        next_cursor = (
            RequirementCursor(created_at=items[-1].created_at, id=items[-1].id).encode()
            if more and items
            else None
        )
        return pagination.Page(items=items, next_cursor=next_cursor)

    async def get(
        self, *, project_id: uuid.UUID, requirement_id: uuid.UUID, user_id: uuid.UUID
    ) -> Requirement:
        async with self._uow as uow:
            await self._access(uow, project_id, user_id, Permission.REQUIREMENT_READ)
            requirement = await uow.requirements.get(project_id, requirement_id)
        if requirement is None:
            raise RequirementNotFound
        return requirement

    async def validate(
        self, *, project_id: uuid.UUID, requirement_id: uuid.UUID, user_id: uuid.UUID
    ) -> ValidationReport:
        """Read-only: checks the current version against today's rules."""
        return validate_requirement(
            await self.get(project_id=project_id, requirement_id=requirement_id, user_id=user_id)
        )

    async def analyze(self, *, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectAnalysis:
        """Conflicts, completeness, ambiguity and unbounded metrics over the project's draft, active
        and satisfied requirements (the first MAX_ANALYZED_REQUIREMENTS by number)."""
        async with self._uow as uow:
            await self._access(uow, project_id, user_id, Permission.REQUIREMENT_READ)
            rows = await uow.requirements.list_by_status(
                project_id, ANALYZED_STATUSES, limit=MAX_ANALYZED_REQUIREMENTS + 1
            )
        return analyze(rows[:MAX_ANALYZED_REQUIREMENTS], truncated=len(rows) > MAX_ANALYZED_REQUIREMENTS)

    async def create(  # noqa: PLR0913 - keyword-only, one argument per field of the request
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        type: RequirementType,
        category: str,
        title: str,
        statement: str,
        priority: RequirementPriority,
        status: RequirementStatus = RequirementStatus.DRAFT,
        source: RequirementSource = RequirementSource.USER,
        confidence: object = None,
        structured_data: object = None,
    ) -> Requirement:
        new = NewRequirement.create(
            project_id=project_id,
            created_by_user_id=user_id,
            type=type,
            category=category,
            title=title,
            statement=statement,
            priority=priority,
            status=status,
            source=source,
            confidence=confidence,
            structured_data=structured_data,
        )
        async with self._uow as uow:
            access = await self._access(uow, project_id, user_id, Permission.REQUIREMENT_CREATE, lock=True)
            requirement = await uow.requirements.add(new)
            await uow.audit.record(
                _event(
                    AuditAction.REQUIREMENT_CREATED,
                    access,
                    requirement,
                    user_id,
                    {"status": requirement.content.status.value},
                )
            )
        return requirement

    async def update(
        self,
        *,
        project_id: uuid.UUID,
        requirement_id: uuid.UUID,
        user_id: uuid.UUID,
        expected_version: int,
        changes: RequirementChanges,
        change_reason: str | None = None,
    ) -> Requirement:
        """Appends a version. Returns the requirement unchanged (no version, no audit) when the
        changes leave every field as it is."""
        async with self._uow as uow:
            access = await self._access(uow, project_id, user_id, Permission.REQUIREMENT_UPDATE, lock=True)
            current = await uow.requirements.get(project_id, requirement_id, for_update=True)
            if current is None:
                raise RequirementNotFound
            revision = current.revise(
                expected_version=expected_version,
                changes=changes,
                change_reason=change_reason,
                author_user_id=user_id,
            )
            if revision is None:
                return current
            saved = await uow.requirements.save(revision)
            for event in _revision_events(access, current, revision, saved, user_id):
                await uow.audit.record(event)
        return saved

    async def delete(self, *, project_id: uuid.UUID, requirement_id: uuid.UUID, user_id: uuid.UUID) -> None:
        async with self._uow as uow:
            access = await self._access(uow, project_id, user_id, Permission.REQUIREMENT_DELETE, lock=True)
            current = await uow.requirements.get(project_id, requirement_id, for_update=True)
            if current is None:
                raise RequirementNotFound
            await uow.requirements.save_deleted(current.delete(self._clock()))
            await uow.audit.record(_event(AuditAction.REQUIREMENT_DELETED, access, current, user_id, {}))

    @staticmethod
    async def _access(
        uow: UnitOfWork,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        permission: Permission,
        *,
        lock: bool = False,
    ) -> ProjectAccess:
        access = await uow.projects.get_for_member(project_id, user_id=user_id, for_update=lock)
        if access is None:
            raise ProjectNotFound
        access.membership.require(permission)
        if lock:
            access.project.ensure_modifiable()
        return access


def _event(
    action: AuditAction,
    access: ProjectAccess,
    requirement: Requirement,
    user_id: uuid.UUID,
    extra: dict[str, Any],
) -> AuditEvent:
    """Identifiers and field names only: requirement text may describe sensitive systems, and it is
    kept in the version history, where the same authorization applies."""
    return AuditEvent(
        action,
        actor_user_id=user_id,
        organization_id=access.project.organization_id,
        resource_type="requirement",
        resource_id=requirement.id,
        metadata={
            "project_id": str(access.project.id),
            "reference": requirement.reference,
            "version": requirement.version,
        }
        | extra,
    )


def _revision_events(
    access: ProjectAccess, before: Requirement, revision: Revision, saved: Requirement, user_id: uuid.UUID
) -> list[AuditEvent]:
    old, new = before.content, saved.content
    fields = [
        name
        for name in ("category", "title", "statement", "priority", "structured_data")
        if getattr(old, name) != getattr(new, name)
    ]
    events = [
        _event(
            AuditAction.REQUIREMENT_VERSION_CREATED,
            access,
            saved,
            user_id,
            {"change_reason_given": revision.change_reason is not None},
        )
    ]
    if fields:
        events.append(_event(AuditAction.REQUIREMENT_UPDATED, access, saved, user_id, {"fields": fields}))
    if old.status is not new.status:
        events.append(
            _event(
                AuditAction.REQUIREMENT_STATUS_CHANGED,
                access,
                saved,
                user_id,
                {"from": old.status.value, "to": new.status.value},
            )
        )
    return events
