"""Architecture use cases: create, read, revise, lay out and compare a project's architecture.

Every write holds the project row (SHARE: archiving waits, archived projects are frozen) and a
revision additionally locks the architecture row, so revisions are strictly sequential: an edit
based on anything but the current revision is refused (``ArchitectureVersionConflict``) instead of
silently overwriting someone else's change. Revision and architecture change in one transaction.

Requirement references are checked against the project's requirements, and a revision's
requirement set must belong to the project. Audit entries carry identifiers and counts only,
never names or configuration from the architecture.
"""

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from core.architecture_ir.commands import Command, apply_commands
from core.architecture_ir.diff import ArchitectureDiff, ChangeKind
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.validation import requirement_problems
from core.domain import pagination
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.requirements.errors import RequirementSetNotFound
from core.domain.unit_of_work import UnitOfWork

from .entities import (
    Architecture,
    ArchitectureLayout,
    NewArchitecture,
    Position,
    RevisionSummary,
    check_positions,
)
from .errors import ArchitectureNotFound, ArchitectureRevisionNotFound, ArchitectureVersionConflict
from .versions import ArchitectureRevision, RevisionSource, compare, first_revision, next_revision

_REVISION_CURSOR = "architecture_revisions"

type Builder = Callable[[ArchitectureRevision], ArchitectureIR]


def encode_revision_cursor(number: int) -> str:
    return pagination.encode_cursor([_REVISION_CURSOR, str(number)])


def decode_revision_cursor(raw: str) -> int:
    kind, number = pagination.decode_cursor(raw, length=2)
    if kind != _REVISION_CURSOR or not number.isdigit() or int(number) < 1:
        raise pagination.InvalidCursor
    return int(number)


def _referenced_requirements(ir: ArchitectureIR) -> set[uuid.UUID]:
    refs = [
        *ir.requirement_refs,
        *(r for n in ir.nodes for r in n.requirement_refs),
        *(r for c in ir.connections for r in c.requirement_refs),
        *(r for a in ir.assumptions for r in a.requirement_refs),
    ]
    return {r.requirement_id for r in refs}


@dataclass(frozen=True, slots=True)
class Revised:
    """The outcome of a revision: the architecture, its new current revision, what changed, and
    the layout (read in the same transaction)."""

    architecture: Architecture
    revision: ArchitectureRevision
    changes: ArchitectureDiff
    layout: ArchitectureLayout


class ArchitectureService:
    def __init__(self, uow: UnitOfWork, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._clock = clock

    async def authorize(self, *, project_id: uuid.UUID, user_id: uuid.UUID, permission: Permission) -> None:
        """Refuses a caller without ``permission`` on the project (404 outside its organization,
        403 without the permission), so the API checks access *before* doing any work on a
        request body (parsing and validating a whole architecture). The write itself checks again
        in its own transaction."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, permission)

    # --- reading ---------------------------------------------------------------------------------

    async def current(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[Architecture, ArchitectureRevision, ArchitectureLayout]:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id)
            revision = await uow.architectures.get_revision(project_id, architecture.current_revision)
            layout = await uow.architectures.get_layout(project_id)
        if revision is None:  # a database constraint guarantees it exists
            raise ArchitectureRevisionNotFound
        return architecture, revision, layout

    async def revision(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, number: int
    ) -> ArchitectureRevision:
        _, revision, _ = await self.version(project_id=project_id, user_id=user_id, number=number)
        return revision

    async def version(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, number: int
    ) -> tuple[Architecture, ArchitectureRevision, ArchitectureLayout]:
        """A past (or the current) revision, with the architecture and its layout."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id)
            revision = await uow.architectures.get_revision(project_id, number)
            layout = await uow.architectures.get_layout(project_id)
        if revision is None:
            raise ArchitectureRevisionNotFound
        return architecture, revision, layout

    async def history(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, cursor: str | None = None, limit: int = 50
    ) -> pagination.Page[RevisionSummary]:
        size = pagination.page_size(limit)
        before = decode_revision_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            await _architecture(uow, project_id)
            rows = await uow.architectures.list_revisions(project_id, before=before, limit=size + 1)
        items, more = rows[:size], len(rows) > size
        next_cursor = encode_revision_cursor(items[-1].number) if more else None
        return pagination.Page(items=items, next_cursor=next_cursor)

    async def compare(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, from_number: int, to_number: int
    ) -> tuple[ArchitectureRevision, ArchitectureRevision, ArchitectureDiff]:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            await _architecture(uow, project_id)
            before = await uow.architectures.get_revision(project_id, from_number)
            after = await uow.architectures.get_revision(project_id, to_number)
        if before is None or after is None:
            raise ArchitectureRevisionNotFound
        return before, after, compare(before, after)

    # --- writing ---------------------------------------------------------------------------------

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        ir: ArchitectureIR,
        source: RevisionSource = RevisionSource.USER,
        reason: str | None = None,
        requirement_set_id: uuid.UUID | None = None,
    ) -> tuple[Architecture, ArchitectureRevision]:
        """The project's architecture, starting at revision 1 with ``ir``."""
        architecture_id = uuid.uuid7()
        first = first_revision(
            architecture_id,
            ir,
            source=source,
            created_by_user_id=user_id,
            reason=reason,
            requirement_set_id=requirement_set_id,
        )
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_CREATE, lock=ProjectLock.SHARE
            )
            await _check_references(uow, project_id, ir, requirement_set_id)
            architecture, revision = await uow.architectures.add(
                NewArchitecture(architecture_id, project_id, user_id), first
            )
            await _record(uow, access, AuditAction.ARCHITECTURE_CREATED, user_id, architecture, revision)
        return architecture, revision

    async def edit(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        base_version: int,
        commands: Sequence[Command],
        reason: str | None = None,
    ) -> Revised:
        """A new revision: ``commands`` applied to revision ``base_version``, which must be current.
        Every field they set is attributed to this user's edit."""
        edit = Provenance(ProvenanceSource.USER_EDIT, actor=f"user:{user_id}", recorded_at=self._clock())
        return await self._revise(
            project_id,
            user_id,
            base_version,
            lambda parent: apply_commands(parent.ir, commands, provenance=edit),
            source=RevisionSource.USER,
            reason=reason,
        )

    async def replace(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        base_version: int,
        ir: ArchitectureIR,
        source: RevisionSource,
        reason: str | None = None,
        requirement_set_id: uuid.UUID | None = None,
    ) -> Revised:
        """A new revision holding ``ir`` as a whole: an approved proposal, an import, a discovery
        or the Architecture Engine's output. Same concurrency rule as ``edit``."""
        return await self._revise(
            project_id,
            user_id,
            base_version,
            lambda _parent: ir,
            source=source,
            reason=reason,
            requirement_set_id=requirement_set_id,
        )

    async def save_layout(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, positions: Mapping[str, Position]
    ) -> ArchitectureLayout:
        """Where nodes are drawn. Never creates a revision, and is not audited: moving a box is not
        an architecture change. Positions must name nodes of the current revision."""
        async with self._uow as uow:
            await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_UPDATE, lock=ProjectLock.SHARE
            )
            architecture = await _architecture(uow, project_id)
            current = await uow.architectures.get_revision(project_id, architecture.current_revision)
            node_ids = frozenset(n.id for n in current.ir.nodes) if current else frozenset()
            check_positions(positions, node_ids)
            return await uow.architectures.save_layout(architecture, positions, user_id)

    async def _revise(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        base_version: int,
        build: Builder,
        *,
        source: RevisionSource,
        reason: str | None,
        requirement_set_id: uuid.UUID | None = None,
    ) -> Revised:
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_UPDATE, lock=ProjectLock.SHARE
            )
            architecture = await _architecture(uow, project_id, for_update=True)
            if architecture.current_revision != base_version:
                raise ArchitectureVersionConflict(details={"latest_version": architecture.current_revision})
            parent = await uow.architectures.get_revision(project_id, base_version)
            if parent is None:
                raise ArchitectureRevisionNotFound
            ir = build(parent)
            await _check_references(uow, project_id, ir, requirement_set_id)
            new, changes = next_revision(
                parent,
                ir,
                source=source,
                created_by_user_id=user_id,
                reason=reason,
                requirement_set_id=requirement_set_id,
            )
            architecture, revision = await uow.architectures.add_revision(architecture, new)
            await _record(
                uow, access, AuditAction.ARCHITECTURE_REVISED, user_id, architecture, revision, changes
            )
            layout = await uow.architectures.get_layout(project_id)
        return Revised(architecture, revision, changes, layout)


async def _architecture(uow: UnitOfWork, project_id: uuid.UUID, *, for_update: bool = False) -> Architecture:
    architecture = await uow.architectures.get(project_id, for_update=for_update)
    if architecture is None:
        raise ArchitectureNotFound
    return architecture


async def _check_references(
    uow: UnitOfWork, project_id: uuid.UUID, ir: ArchitectureIR, requirement_set_id: uuid.UUID | None
) -> None:
    if (
        requirement_set_id is not None
        and await uow.requirement_sets.get(project_id, requirement_set_id) is None
    ):
        raise RequirementSetNotFound
    referenced = _referenced_requirements(ir)
    if not referenced:
        return
    found = await uow.requirements.list_by_ids(project_id, sorted(referenced))
    problems = requirement_problems(ir, {r.id: r.version for r in found})
    if problems:
        raise InvalidArchitecture(problems)


async def _record(
    uow: UnitOfWork,
    access: ProjectAccess,
    action: AuditAction,
    user_id: uuid.UUID,
    architecture: Architecture,
    revision: ArchitectureRevision,
    changes: ArchitectureDiff | None = None,
) -> None:
    metadata: dict[str, object] = {
        "project_id": str(architecture.project_id),
        "revision": revision.number,
        "source": revision.source.value,
        "node_count": len(revision.ir.nodes),
        "connection_count": len(revision.ir.connections),
        "content_sha256": revision.content_hash,  # a public digest, named to pass the secret-key guard
    }
    if changes is not None:
        for kind in ChangeKind:
            metadata[f"elements_{kind.value}"] = sum(c.change is kind for c in changes.changes())
    await uow.audit.record(
        AuditEvent(
            action,
            actor_user_id=user_id,
            organization_id=access.project.organization_id,
            resource_type="architecture",
            resource_id=architecture.id,
            metadata=metadata,
        )
    )
