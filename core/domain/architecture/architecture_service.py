"""Architecture use cases: create, list, read, update metadata, revise content, restore a revision,
archive, restore, delete, lay out and compare the architectures of a project.

Every operation reaches an architecture through its project (``project_access``: 404 outside the
organization, 403 without the permission) and then by (project, architecture) id, so no id from
another project or tenant can reach anything. Writes hold the project row (SHARE: archiving the
project waits, archived projects are frozen) and lock the architecture row, so metadata changes,
revisions and lifecycle changes of one architecture are serialized.

Content changes (commands, a whole new IR, restoring an earlier revision) create a new immutable
revision from the current one, which must be the one the change was based on
(``ArchitectureVersionConflict`` otherwise: nothing is merged or overwritten silently). A change
that leaves the content as it is creates no revision and succeeds (``Revised.created`` is false).
Metadata (name, description) and lifecycle changes create no revision.

Requirement references are checked against the project's requirements, and a requirement set must
belong to the project. Audit entries carry identifiers and counts only, never names or content.
"""

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from core.architecture_ir.commands import Command, apply_commands
from core.architecture_ir.diff import ArchitectureDiff, ChangeKind
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.validation import requirement_problems
from core.domain import pagination
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.errors import NothingToUpdate
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.requirements.errors import RequirementSetNotFound
from core.domain.unit_of_work import UnitOfWork

from .entities import (
    Architecture,
    ArchitectureLayout,
    ArchitectureQuery,
    ArchitectureStatus,
    NewArchitecture,
    Position,
    RevisionSummary,
    check_positions,
    normalize_architecture_description,
    normalize_architecture_name,
)
from .errors import (
    ArchitectureNotFound,
    ArchitectureRevisionNotFound,
    ArchitectureUnchanged,
    ArchitectureVersionConflict,
)
from .versions import (
    ArchitectureRevision,
    NewRevision,
    RevisionSource,
    compare,
    first_revision,
    next_revision,
    restored_revision,
)

_REVISION_CURSOR = "architecture_revisions"
_LIST_CURSOR = "architectures"

type Builder = Callable[[ArchitectureRevision], tuple[NewRevision, ArchitectureDiff]]


def encode_revision_cursor(number: int) -> str:
    return pagination.encode_cursor([_REVISION_CURSOR, str(number)])


def decode_revision_cursor(raw: str) -> int:
    kind, number = pagination.decode_cursor(raw, length=2)
    if kind != _REVISION_CURSOR or not number.isdigit() or int(number) < 1:
        raise pagination.InvalidCursor
    return int(number)


def encode_list_cursor(architecture: Architecture) -> str:
    return pagination.encode_cursor([_LIST_CURSOR, architecture.created_at.isoformat(), str(architecture.id)])


def decode_list_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    kind, created_at, architecture_id = pagination.decode_cursor(raw, length=3)
    if kind != _LIST_CURSOR:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(created_at), uuid.UUID(architecture_id)
    except ValueError:
        raise pagination.InvalidCursor from None


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
    """The outcome of a content change. ``created`` is false when the content did not change: then
    ``revision`` is the (unchanged) current revision and ``changes`` is empty."""

    architecture: Architecture
    revision: ArchitectureRevision
    changes: ArchitectureDiff
    layout: ArchitectureLayout
    created: bool = True


@dataclass(frozen=True, slots=True)
class History:
    architecture: Architecture
    page: pagination.Page[RevisionSummary]


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

    async def list(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        status: ArchitectureStatus | None = None,
        search: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[Architecture]:
        size = pagination.page_size(limit)
        query = ArchitectureQuery(
            status=status,
            search=(search.strip() or None) if search else None,
            after=decode_list_cursor(cursor) if cursor else None,
            limit=size + 1,
        )
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            rows = await uow.architectures.list_for_project(project_id, query)
        items, more = rows[:size], len(rows) > size
        return pagination.Page(items=items, next_cursor=encode_list_cursor(items[-1]) if more else None)

    async def get(
        self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[Architecture, ArchitectureRevision, ArchitectureLayout]:
        """The architecture with its current revision and layout."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id, architecture_id)
            revision = await _revision(uow, architecture, architecture.current_revision)
            layout = await uow.architectures.get_layout(architecture.id)
        return architecture, revision, layout

    async def version(
        self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, user_id: uuid.UUID, number: int
    ) -> tuple[Architecture, ArchitectureRevision, ArchitectureLayout]:
        """One revision exactly as it was created, with the architecture and its layout."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id, architecture_id)
            revision = await _revision(uow, architecture, number)
            layout = await uow.architectures.get_layout(architecture.id)
        return architecture, revision, layout

    async def history(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        cursor: str | None = None,
        limit: int = 50,
    ) -> History:
        size = pagination.page_size(limit)
        before = decode_revision_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id, architecture_id)
            rows = await uow.architectures.list_revisions(architecture.id, before=before, limit=size + 1)
        items, more = rows[:size], len(rows) > size
        next_cursor = encode_revision_cursor(items[-1].number) if more else None
        return History(architecture, pagination.Page(items=items, next_cursor=next_cursor))

    async def compare(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        from_number: int,
        to_number: int,
    ) -> tuple[ArchitectureRevision, ArchitectureRevision, ArchitectureDiff]:
        """Both revisions must belong to this architecture (a number is only ever looked up
        within it)."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id, architecture_id)
            before = await _revision(uow, architecture, from_number)
            after = await _revision(uow, architecture, to_number)
        return before, after, compare(before, after)

    # --- creating --------------------------------------------------------------------------------

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        name: str,
        description: str = "",
        ir: ArchitectureIR | None = None,
        source: RevisionSource = RevisionSource.USER,
        reason: str | None = None,
        requirement_set_id: uuid.UUID | None = None,
    ) -> tuple[Architecture, ArchitectureRevision]:
        """A new architecture of the project, starting at revision 1: ``ir``, or an empty
        architecture (no nodes; the valid starting point of every design)."""
        clean_name = normalize_architecture_name(name)
        clean_description = normalize_architecture_description(description)
        content = ir if ir is not None else ArchitectureIR(clean_name, clean_description or None)
        architecture_id = uuid.uuid7()
        first = first_revision(
            architecture_id,
            content,
            source=source,
            created_by_user_id=user_id,
            reason=reason,
            requirement_set_id=requirement_set_id,
        )
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_CREATE, lock=ProjectLock.SHARE
            )
            await _check_references(uow, project_id, content, requirement_set_id)
            architecture, revision = await uow.architectures.add(
                NewArchitecture(architecture_id, project_id, clean_name, clean_description, user_id), first
            )
            await _record(
                uow,
                access,
                AuditAction.ARCHITECTURE_CREATED,
                user_id,
                architecture,
                _revision_facts(revision),
            )
        return architecture, revision

    # --- metadata and lifecycle (no revision) ----------------------------------------------------

    async def update_metadata(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        name: str | None = None,
        description: str | None = None,
    ) -> Architecture:
        """Name and description. Creates no revision: they describe the record, not the design."""
        if name is None and description is None:
            raise NothingToUpdate
        async with self._uow as uow:
            access = await _write_access(uow, project_id, user_id, Permission.ARCHITECTURE_UPDATE)
            architecture = await _architecture(uow, project_id, architecture_id, for_update=True)
            updated = architecture.with_metadata(name=name, description=description, by=user_id)
            changed = [f for f in ("name", "description") if getattr(updated, f) != getattr(architecture, f)]
            if not changed:
                return architecture
            saved = await uow.architectures.save(updated)
            await _record(
                uow, access, AuditAction.ARCHITECTURE_UPDATED, user_id, saved, {"changed_fields": changed}
            )
        return saved

    async def archive(
        self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, user_id: uuid.UUID
    ) -> Architecture:
        """Read-only until restored; idempotent."""
        return await self._lifecycle(
            project_id,
            architecture_id,
            user_id,
            Permission.ARCHITECTURE_UPDATE,
            AuditAction.ARCHITECTURE_ARCHIVED,
            lambda a: a.archive(self._clock(), by=user_id),
        )

    async def restore(
        self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, user_id: uuid.UUID
    ) -> Architecture:
        """Back from the archive; idempotent. (Restoring a *revision* is ``restore_revision``.)"""
        return await self._lifecycle(
            project_id,
            architecture_id,
            user_id,
            Permission.ARCHITECTURE_UPDATE,
            AuditAction.ARCHITECTURE_RESTORED,
            lambda a: a.restore(by=user_id),
        )

    async def delete(self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Soft delete, only from the archived state: afterwards the architecture is not found
        anywhere; its revisions are kept, nothing is purged."""
        await self._lifecycle(
            project_id,
            architecture_id,
            user_id,
            Permission.ARCHITECTURE_DELETE,
            AuditAction.ARCHITECTURE_DELETED,
            lambda a: a.delete(self._clock(), by=user_id),
        )

    async def _lifecycle(
        self,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        permission: Permission,
        action: AuditAction,
        change: Callable[[Architecture], Architecture],
    ) -> Architecture:
        async with self._uow as uow:
            access = await _write_access(uow, project_id, user_id, permission)
            architecture = await _architecture(uow, project_id, architecture_id, for_update=True)
            changed = change(architecture)
            if changed == architecture:
                return architecture  # idempotent, nothing recorded
            saved = await uow.architectures.save(changed)
            await _record(uow, access, action, user_id, saved, {"status": saved.status.value})
        return saved

    # --- content (a new revision) ----------------------------------------------------------------

    async def edit(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        base_version: int,
        commands: Sequence[Command],
        reason: str | None = None,
    ) -> Revised:
        """``commands`` applied to revision ``base_version``, which must be current. Every field
        they change is attributed to this user's edit."""
        edit = Provenance(ProvenanceSource.USER_EDIT, actor=f"user:{user_id}", recorded_at=self._clock())

        def build(parent: ArchitectureRevision) -> tuple[NewRevision, ArchitectureDiff]:
            ir = apply_commands(parent.ir, commands, provenance=edit)
            return next_revision(
                parent, ir, source=RevisionSource.USER, created_by_user_id=user_id, reason=reason
            )

        return await self._revise(project_id, architecture_id, user_id, base_version, build, None)

    async def replace(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        base_version: int,
        ir: ArchitectureIR,
        source: RevisionSource = RevisionSource.USER,
        reason: str | None = None,
        requirement_set_id: uuid.UUID | None = None,
    ) -> Revised:
        """A whole new content: a full save, an import, an approved proposal, a discovery, the
        Architecture Engine's output. Same concurrency rule as ``edit``."""

        def build(parent: ArchitectureRevision) -> tuple[NewRevision, ArchitectureDiff]:
            return next_revision(
                parent,
                ir,
                source=source,
                created_by_user_id=user_id,
                reason=reason,
                requirement_set_id=requirement_set_id,
            )

        return await self._revise(
            project_id, architecture_id, user_id, base_version, build, requirement_set_id
        )

    async def restore_revision(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        number: int,
        base_version: int,
        reason: str | None = None,
    ) -> Revised:
        """A new revision whose content is revision ``number``'s. Every revision in between is
        kept; the new one records where its content came from (``restored_from``)."""
        restored: list[ArchitectureRevision] = []

        def build(parent: ArchitectureRevision) -> tuple[NewRevision, ArchitectureDiff]:
            return restored_revision(parent, restored[0], created_by_user_id=user_id, reason=reason)

        async def load(uow: UnitOfWork, architecture: Architecture) -> None:
            restored.append(await _revision(uow, architecture, number))

        return await self._revise(project_id, architecture_id, user_id, base_version, build, None, load)

    async def save_layout(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        positions: Mapping[str, Position],
    ) -> ArchitectureLayout:
        """Where nodes are drawn. Never creates a revision, and is not audited: moving a box is not
        an architecture change. Positions must name nodes of the current revision."""
        async with self._uow as uow:
            await _write_access(uow, project_id, user_id, Permission.ARCHITECTURE_UPDATE)
            architecture = await _architecture(uow, project_id, architecture_id)
            architecture.ensure_modifiable()
            current = await _revision(uow, architecture, architecture.current_revision)
            check_positions(positions, frozenset(n.id for n in current.ir.nodes))
            return await uow.architectures.save_layout(architecture, positions, user_id)

    async def _revise(
        self,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        base_version: int,
        build: Builder,
        requirement_set_id: uuid.UUID | None,
        load: Callable[[UnitOfWork, Architecture], object] | None = None,
    ) -> Revised:
        async with self._uow as uow:
            access = await _write_access(uow, project_id, user_id, Permission.ARCHITECTURE_UPDATE)
            architecture = await _architecture(uow, project_id, architecture_id, for_update=True)
            architecture.ensure_modifiable()
            if load is not None:
                await load(uow, architecture)  # type: ignore[misc]
            if architecture.current_revision != base_version:
                raise ArchitectureVersionConflict(details={"latest_version": architecture.current_revision})
            parent = await _revision(uow, architecture, base_version)
            try:
                new, changes = build(parent)
            except ArchitectureUnchanged:
                layout = await uow.architectures.get_layout(architecture.id)
                return Revised(architecture, parent, ArchitectureDiff(), layout, created=False)
            await _check_references(uow, project_id, new.ir, requirement_set_id)
            architecture, revision = await uow.architectures.add_revision(architecture, new)
            facts = _revision_facts(revision) | _change_counts(changes)
            action = AuditAction.ARCHITECTURE_REVISED
            if revision.restored_from is not None:
                facts["restored_from"] = revision.restored_from
                action = AuditAction.ARCHITECTURE_REVISION_RESTORED
            await _record(uow, access, action, user_id, architecture, facts)
            layout = await uow.architectures.get_layout(architecture.id)
        return Revised(architecture, revision, changes, layout)


async def _write_access(
    uow: UnitOfWork, project_id: uuid.UUID, user_id: uuid.UUID, permission: Permission
) -> ProjectAccess:
    return await project_access(uow, project_id, user_id, permission, lock=ProjectLock.SHARE)


async def _architecture(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, *, for_update: bool = False
) -> Architecture:
    architecture = await uow.architectures.get(project_id, architecture_id, for_update=for_update)
    if architecture is None:
        raise ArchitectureNotFound
    return architecture


async def _revision(uow: UnitOfWork, architecture: Architecture, number: int) -> ArchitectureRevision:
    """Only ever looked up within an architecture already found through its project."""
    revision = await uow.architectures.get_revision(architecture.id, number)
    if revision is None:
        raise ArchitectureRevisionNotFound
    return revision


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


def _revision_facts(revision: ArchitectureRevision) -> dict[str, object]:
    return {
        "revision": revision.number,
        "source": revision.source.value,
        "node_count": len(revision.ir.nodes),
        "connection_count": len(revision.ir.connections),
        "content_sha256": revision.content_hash,  # a public digest, named to pass the secret-key guard
    }


def _change_counts(changes: ArchitectureDiff) -> dict[str, object]:
    return {f"elements_{kind.value}": sum(c.change is kind for c in changes.changes()) for kind in ChangeKind}


async def _record(
    uow: UnitOfWork,
    access: ProjectAccess,
    action: AuditAction,
    user_id: uuid.UUID,
    architecture: Architecture,
    facts: dict[str, object],
) -> None:
    await uow.audit.record(
        AuditEvent(
            action,
            actor_user_id=user_id,
            organization_id=access.project.organization_id,
            resource_type="architecture",
            resource_id=architecture.id,
            metadata={"project_id": str(architecture.project_id), **facts},
        )
    )
