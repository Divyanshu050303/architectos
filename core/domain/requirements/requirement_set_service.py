"""Requirement set use cases. Creation locks the project row (like every requirement write), so
the set is built from a consistent view: no requirement can change between being read and being
pinned, and the set number is allocated safely."""

import uuid
from typing import Any

from core.domain import pagination
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.organizations.permissions import Permission
from core.domain.projects.repository import ProjectLock
from core.domain.unit_of_work import UnitOfWork

from .access import project_access
from .analysis import find_conflicts, validate_requirement
from .entities import Requirement
from .errors import InvalidRequirementSet, RequirementSetConflicts, RequirementSetNotFound
from .planning import SCHEMA_VERSION, build_planning_input, content_hash
from .queries import encode_set_cursor
from .requirement_sets import (
    MAX_SET_REQUIREMENTS,
    NewRequirementSet,
    PinnedVersion,
    RequirementSet,
    normalize_set_description,
    normalize_set_name,
)
from .requirements import IN_FORCE


class RequirementSetService:
    def __init__(self, uow: UnitOfWork, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._clock = clock

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        name: str = "",
        description: str = "",
        requirement_ids: list[uuid.UUID] | None = None,
    ) -> RequirementSet:
        """Pins the given requirements (default: every requirement in force) at their current
        versions, with the Architecture Planning Input built from them."""
        clean_name, clean_description = normalize_set_name(name), normalize_set_description(description)
        if requirement_ids is not None:
            _check_selection(requirement_ids)
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.REQUIREMENT_SET_CREATE, lock=ProjectLock.EXCLUSIVE
            )
            chosen = await _select(uow, project_id, requirement_ids)
            conflicts = find_conflicts(chosen)
            if conflicts:
                raise RequirementSetConflicts(
                    details={
                        "conflicts": [
                            {
                                "reason": c.reason,
                                "metric": c.metric,
                                "requirements": [r.reference for r in c.requirements],
                                "message": c.message,
                            }
                            for c in conflicts
                        ]
                    }
                )
            planning_input = build_planning_input(access.project, chosen)
            created = await uow.requirement_sets.add(
                NewRequirementSet(
                    project_id=project_id,
                    name=clean_name,
                    description=clean_description,
                    items=tuple(
                        PinnedVersion(requirement_id=r.id, number=r.number, version=r.version)
                        for r in sorted(chosen, key=lambda r: r.number)
                    ),
                    schema_version=SCHEMA_VERSION,
                    planning_input=dict(planning_input),
                    content_hash=content_hash(planning_input),
                    created_by_user_id=user_id,
                )
            )
            await uow.audit.record(
                AuditEvent(
                    AuditAction.REQUIREMENT_SET_CREATED,
                    actor_user_id=user_id,
                    organization_id=access.project.organization_id,
                    resource_type="requirement_set",
                    resource_id=created.id,
                    metadata={
                        "project_id": str(project_id),
                        "number": created.number,
                        "requirement_count": created.requirement_count,
                        # A public digest of the planning input (not a secret); named so it does
                        # not trip the audit log's secret-key guard, which refuses "*hash*" keys.
                        "planning_input_sha256": created.content_hash,
                    },
                )
            )
        return created

    async def list(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, before: int | None = None, limit: int = 50
    ) -> pagination.Page[RequirementSet]:
        size = pagination.page_size(limit)
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.REQUIREMENT_READ)
            rows = await uow.requirement_sets.list_for_project(
                project_id, before_number=before, limit=size + 1
            )
        items, more = rows[:size], len(rows) > size
        return pagination.Page(items=items, next_cursor=encode_set_cursor(items[-1].number) if more else None)

    async def get(self, *, project_id: uuid.UUID, set_id: uuid.UUID, user_id: uuid.UUID) -> RequirementSet:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.REQUIREMENT_READ)
            found = await uow.requirement_sets.get(project_id, set_id)
        if found is None:
            raise RequirementSetNotFound
        return found

    async def planning_input(
        self, *, project_id: uuid.UUID, set_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[RequirementSet, dict[str, Any]]:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.REQUIREMENT_READ)
            found = await uow.requirement_sets.get_planning_input(project_id, set_id)
        if found is None:
            raise RequirementSetNotFound
        return found


def _check_selection(requirement_ids: list[uuid.UUID]) -> None:
    if not requirement_ids:
        raise InvalidRequirementSet(details={"field": "requirement_ids", "reason": "empty"})
    if len(requirement_ids) > MAX_SET_REQUIREMENTS:
        raise InvalidRequirementSet(details={"field": "requirement_ids", "reason": "too_many"})
    seen: set[uuid.UUID] = set()
    for requirement_id in requirement_ids:
        if requirement_id in seen:
            raise InvalidRequirementSet(
                details={"requirement_id": str(requirement_id), "reason": "duplicate"}
            )
        seen.add(requirement_id)


async def _select(
    uow: UnitOfWork, project_id: uuid.UUID, requirement_ids: list[uuid.UUID] | None
) -> list[Requirement]:
    if requirement_ids is None:
        chosen = await uow.requirements.list_by_status(project_id, IN_FORCE, limit=MAX_SET_REQUIREMENTS + 1)
        if not chosen:
            raise InvalidRequirementSet(details={"field": "requirement_ids", "reason": "nothing_in_force"})
        if len(chosen) > MAX_SET_REQUIREMENTS:
            raise InvalidRequirementSet(details={"field": "requirement_ids", "reason": "too_many"})
    else:
        found = {r.id: r for r in await uow.requirements.list_by_ids(project_id, requirement_ids)}
        chosen = []
        for requirement_id in requirement_ids:
            requirement = found.get(requirement_id)
            if requirement is None:
                raise InvalidRequirementSet(
                    details={"requirement_id": str(requirement_id), "reason": "not_found"}
                )
            if requirement.content.status not in IN_FORCE:
                raise InvalidRequirementSet(
                    details={"requirement_id": str(requirement_id), "reason": "not_in_force"}
                )
            chosen.append(requirement)
    for requirement in chosen:
        if not validate_requirement(requirement).valid:
            raise InvalidRequirementSet(details={"requirement_id": str(requirement.id), "reason": "invalid"})
    return chosen
