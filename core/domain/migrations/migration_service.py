"""Migration plan use cases: generate a plan between an exact source revision and an exact target (a
later revision of the same architecture, or an evolution candidate on the source revision), read it
with its staleness, list plans, read a plan's history, regenerate or revise it, and record a
person's review — submit, approve or reject an exact version, archive.

Generating is synchronous, in three steps like the analyses: read and authorize (a short
transaction: the architecture, the revisions or the candidate, and the other engines' stored
analyses), plan (on a worker thread, no transaction, no lock: every input is immutable), then store
— re-authorized under the project lock — and audit. The architecture is never changed, and nothing
is executed: there is no execution endpoint, status or record.

Access: generating, regenerating, submitting and archiving need ``migration.plan``; approving and
rejecting need ``migration.approve``; reading needs ``architecture.read``. Every lookup goes project
-> architecture -> plan; a plan of a deleted architecture is not found. A version's staleness is
computed on read, never stored. Audit entries carry identifiers and counts only.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime

from core.domain import pagination
from core.domain.architecture.entities import Architecture
from core.domain.architecture.errors import ArchitectureNotFound, ArchitectureRevisionNotFound
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.evolution.errors import CandidateNotFound, EvolutionAnalysisNotFound, InvalidCandidate
from core.domain.evolution.overlays import apply_candidate
from core.domain.evolution.values import EvidenceState
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.unit_of_work import UnitOfWork

from .entities import MigrationPlanVersion, MigrationRequest
from .errors import MigrationPlanNotFound
from .loading import load_evidence
from .plans import SourceRef
from .ports import MigrationPlanner, PlanningInputs, TargetCandidate, TargetRevision
from .values import PlanStatus, TargetKind
from .versioning import (
    CurrentState,
    Freshness,
    approve,
    archive,
    first_version,
    freshness,
    regenerate,
    reject,
    submit,
)

MAX_PAGE = 100
_LISTING = "migration_plans"


@dataclass(frozen=True, slots=True)
class PlanView:
    """A version as read: its content and review, and whether it is still current."""

    version: MigrationPlanVersion
    freshness: Freshness


def encode_plan_cursor(created_at: datetime, version_id: uuid.UUID) -> str:
    return pagination.encode_cursor([_LISTING, created_at.isoformat(), str(version_id)])


def decode_plan_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    sort, created_at, version_id = pagination.decode_cursor(raw, length=3)
    if sort != _LISTING:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(created_at), uuid.UUID(version_id)
    except ValueError:
        raise pagination.InvalidCursor from None


class MigrationPlanService:
    def __init__(self, uow: UnitOfWork, planner: MigrationPlanner, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._planner = planner
        self._clock = clock

    # --- generating ------------------------------------------------------------------------------

    async def create(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, request: MigrationRequest
    ) -> PlanView:
        """Version 1 of a new plan for ``request``. A request the engine refuses (a 4xx) stores nothing."""
        inputs = await self._inputs(project_id, user_id, request)
        proposal = await asyncio.to_thread(self._planner.plan, inputs)
        async with self._uow as uow:
            access = await _writable(
                uow, project_id, user_id, request.architecture_id, Permission.MIGRATION_PLAN
            )
            version = first_version(
                version_id=uuid.uuid7(),
                plan_id=uuid.uuid7(),
                project_id=project_id,
                request=request,
                proposal=proposal,
                user_id=user_id,
                at=self._clock(),
            )
            await uow.migrations.add(version)
            await _audit(uow, access, AuditAction.MIGRATION_PLAN_CREATED, user_id, version)
            current = await self._current(uow, project_id, version)
        return PlanView(version, current)

    async def regenerate(
        self,
        *,
        project_id: uuid.UUID,
        plan_id: uuid.UUID,
        user_id: uuid.UUID,
        request: MigrationRequest | None = None,
        replace_reviewed: bool = False,
    ) -> tuple[PlanView, bool]:
        """A new version from the latest version's request (or a revised ``request``), and whether one
        was created: an identical result creates nothing. A version under review or approved is
        replaced only when ``replace_reviewed``."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.MIGRATION_PLAN)
            latest = await _version(uow, project_id, plan_id, None)
        revised = request or latest.request
        inputs = await self._inputs(project_id, user_id, revised)
        proposal = await asyncio.to_thread(self._planner.plan, inputs)
        async with self._uow as uow:
            access = await _writable(
                uow, project_id, user_id, revised.architecture_id, Permission.MIGRATION_PLAN
            )
            latest = await _version(uow, project_id, plan_id, None, for_update=True)
            result = regenerate(
                latest,
                version_id=uuid.uuid7(),
                request=revised,
                proposal=proposal,
                user_id=user_id,
                at=self._clock(),
                replace_reviewed=replace_reviewed,
            )
            if result.created is not None:
                await uow.migrations.update_review(result.previous)
                await uow.migrations.add(result.created)
                await _audit(uow, access, AuditAction.MIGRATION_PLAN_REGENERATED, user_id, result.created)
            shown = result.created or result.previous
            current = await self._current(uow, project_id, shown)
        return PlanView(shown, current), result.created is not None

    async def _inputs(
        self, project_id: uuid.UUID, user_id: uuid.UUID, request: MigrationRequest
    ) -> PlanningInputs:
        """Read and authorize everything the plan is generated from, in one short transaction."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.MIGRATION_PLAN)
            architecture = await _architecture(uow, project_id, request.architecture_id)
            architecture.ensure_modifiable()
            source = await uow.architectures.get_revision(architecture.id, request.source_revision)
            if source is None:
                raise ArchitectureRevisionNotFound
            spec = request.target
            target: TargetRevision | TargetCandidate
            revisions = [source.number]
            if spec.revision is not None:
                revision = await uow.architectures.get_revision(architecture.id, spec.revision)
                if revision is None:
                    raise ArchitectureRevisionNotFound
                target = TargetRevision(revision.number, revision.content_hash, revision.ir)
                revisions.append(revision.number)
            else:
                analysis_id, candidate_id = spec.analysis_id, spec.candidate_id
                report = (
                    await uow.evolution.get(project_id, architecture.id, analysis_id) if analysis_id else None
                )
                if report is None:
                    raise EvolutionAnalysisNotFound
                candidate = (
                    await uow.evolution.get_candidate(project_id, report.analysis.id, candidate_id)
                    if candidate_id
                    else None
                )
                if candidate is None:
                    raise CandidateNotFound
                target = TargetCandidate(report.analysis.id, candidate)
            analyses = await load_evidence(uow, project_id, architecture.id, tuple(revisions))
        reference = SourceRef(architecture.id, source.number, source.content_hash)
        return PlanningInputs(request, reference, source.ir, target, analyses)

    # --- reading ---------------------------------------------------------------------------------

    async def get(
        self, *, project_id: uuid.UUID, plan_id: uuid.UUID, user_id: uuid.UUID, version: int | None = None
    ) -> PlanView:
        """A version (default: the latest) with its staleness, computed now."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            found = await _version(uow, project_id, plan_id, version)
            return PlanView(found, await self._current(uow, project_id, found))

    async def history(
        self, *, project_id: uuid.UUID, plan_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[MigrationPlanVersion]:
        """Every version of the plan with its review history, by version number."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            latest = await _version(uow, project_id, plan_id, None)
            return await uow.migrations.history(project_id, latest.plan_id)

    async def list_plans(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        architecture_id: uuid.UUID | None = None,
        status: PlanStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[MigrationPlanVersion]:
        """The latest version of each plan of the project, newest first."""
        size = max(1, min(limit, MAX_PAGE))
        after = decode_plan_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            if architecture_id is not None:
                await _architecture(uow, project_id, architecture_id)
            rows = await uow.migrations.list_latest(
                project_id, architecture_id=architecture_id, status=status, after=after, limit=size + 1
            )
            live = {
                a.id
                for a in [
                    await uow.architectures.get(project_id, i)
                    for i in {r.request.architecture_id for r in rows}
                ]
                if a is not None
            }
        page, more = rows[:size], len(rows) > size
        last = page[-1] if more and page else None
        items = [
            r for r in page if r.request.architecture_id in live
        ]  # a deleted architecture hides its plans
        return pagination.Page(
            items=items, next_cursor=encode_plan_cursor(last.created_at, last.id) if last else None
        )

    # --- review ----------------------------------------------------------------------------------

    async def submit(
        self, *, project_id: uuid.UUID, plan_id: uuid.UUID, version: int, user_id: uuid.UUID
    ) -> PlanView:
        async with self._uow as uow:
            access, found = await _reviewable(
                uow, project_id, plan_id, version, user_id, Permission.MIGRATION_PLAN
            )
            current = await self._current(uow, project_id, found)
            moved = submit(found, current=current, user_id=user_id, at=self._clock())
            await uow.migrations.update_review(moved)
            await _audit(uow, access, AuditAction.MIGRATION_PLAN_SUBMITTED, user_id, moved)
        return PlanView(moved, current)

    async def approve(
        self,
        *,
        project_id: uuid.UUID,
        plan_id: uuid.UUID,
        version: int,
        user_id: uuid.UUID,
        fingerprint: str,
        comment: str | None = None,
    ) -> PlanView:
        """A person approves this exact, current version. Nothing is executed."""
        async with self._uow as uow:
            access, found = await _reviewable(
                uow, project_id, plan_id, version, user_id, Permission.MIGRATION_APPROVE
            )
            current = await self._current(uow, project_id, found)
            moved = approve(
                found,
                version_number=version,
                fingerprint=fingerprint,
                current=current,
                user_id=user_id,
                at=self._clock(),
                comment=comment,
            )
            await uow.migrations.update_review(moved)
            await _audit(uow, access, AuditAction.MIGRATION_PLAN_APPROVED, user_id, moved)
        return PlanView(moved, current)

    async def reject(
        self,
        *,
        project_id: uuid.UUID,
        plan_id: uuid.UUID,
        version: int,
        user_id: uuid.UUID,
        fingerprint: str,
        comment: str,
    ) -> PlanView:
        async with self._uow as uow:
            access, found = await _reviewable(
                uow, project_id, plan_id, version, user_id, Permission.MIGRATION_APPROVE
            )
            moved = reject(
                found,
                version_number=version,
                fingerprint=fingerprint,
                comment=comment,
                user_id=user_id,
                at=self._clock(),
            )
            await uow.migrations.update_review(moved)
            await _audit(uow, access, AuditAction.MIGRATION_PLAN_REJECTED, user_id, moved)
            current = await self._current(uow, project_id, moved)
        return PlanView(moved, current)

    async def archive(
        self, *, project_id: uuid.UUID, plan_id: uuid.UUID, version: int, user_id: uuid.UUID
    ) -> PlanView:
        async with self._uow as uow:
            access, found = await _reviewable(
                uow, project_id, plan_id, version, user_id, Permission.MIGRATION_PLAN
            )
            moved = archive(found, user_id=user_id, at=self._clock())
            await uow.migrations.update_review(moved)
            await _audit(uow, access, AuditAction.MIGRATION_PLAN_ARCHIVED, user_id, moved)
            current = await self._current(uow, project_id, moved)
        return PlanView(moved, current)

    # --- staleness -------------------------------------------------------------------------------

    async def _current(
        self, uow: UnitOfWork, project_id: uuid.UUID, version: MigrationPlanVersion
    ) -> Freshness:
        """The version's staleness now: its revisions' (or candidate overlay's) content, the latest
        revision, the planner's versions, and the analyses it cited (immutable once stored)."""
        proposal = version.proposal
        source, target = proposal.source, proposal.target
        architecture = await _architecture(uow, project_id, source.architecture_id)
        revision = await uow.architectures.get_revision(architecture.id, source.revision_number)
        target_hash: str | None = None
        if target.kind is TargetKind.REVISION:
            reached = await uow.architectures.get_revision(architecture.id, target.revision_number)
            target_hash = reached.content_hash if reached else None
        elif revision is not None and target.analysis_id is not None and target.candidate_id is not None:
            report = await uow.evolution.get(project_id, architecture.id, target.analysis_id)
            candidate = (
                await uow.evolution.get_candidate(project_id, report.analysis.id, target.candidate_id)
                if report
                else None
            )
            if candidate is not None:
                try:
                    target_hash = apply_candidate(revision.ir, candidate).content_hash
                except InvalidCandidate:
                    target_hash = None  # it can no longer be rebuilt on its baseline
        cited = {e.key: EvidenceState.CURRENT for e in proposal.evidence if e.state is EvidenceState.CURRENT}
        now = CurrentState(
            revision.content_hash if revision else None,
            target_hash,
            architecture.current_revision,
            self._planner.models(),
            cited,
        )
        return freshness(proposal, now)


async def _architecture(uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID) -> Architecture:
    architecture = await uow.architectures.get(project_id, architecture_id)
    if architecture is None:
        raise ArchitectureNotFound
    return architecture


async def _writable(
    uow: UnitOfWork,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    architecture_id: uuid.UUID,
    permission: Permission,
) -> ProjectAccess:
    access = await project_access(uow, project_id, user_id, permission, lock=ProjectLock.SHARE)
    (await _architecture(uow, project_id, architecture_id)).ensure_modifiable()
    return access


async def _reviewable(
    uow: UnitOfWork,
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    version: int,
    user_id: uuid.UUID,
    permission: Permission,
) -> tuple[ProjectAccess, MigrationPlanVersion]:
    access = await project_access(uow, project_id, user_id, permission, lock=ProjectLock.SHARE)
    found = await _version(uow, project_id, plan_id, version, for_update=True)
    (await _architecture(uow, project_id, found.request.architecture_id)).ensure_modifiable()
    return access, found


async def _version(
    uow: UnitOfWork,
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    version: int | None,
    *,
    for_update: bool = False,
) -> MigrationPlanVersion:
    found = await uow.migrations.get(project_id, plan_id, version, for_update=for_update)
    if found is None or await uow.architectures.get(project_id, found.request.architecture_id) is None:
        raise MigrationPlanNotFound  # a plan of a deleted architecture is no longer reachable
    return found


async def _audit(
    uow: UnitOfWork,
    access: ProjectAccess,
    action: AuditAction,
    user_id: uuid.UUID,
    version: MigrationPlanVersion,
) -> None:
    proposal = version.proposal
    await uow.audit.record(
        AuditEvent(
            action,
            actor_user_id=user_id,
            organization_id=access.project.organization_id,
            resource_type="migration_plan",
            resource_id=version.plan_id,
            metadata={
                "project_id": str(version.project_id),
                "architecture_id": str(proposal.source.architecture_id),
                "plan_id": str(version.plan_id),
                "version": version.version,
                "status": version.status.value,
                "source_revision": proposal.source.revision_number,
                "target_kind": proposal.target.kind.value,
                "target_revision": proposal.target.revision_number,
                "steps": len(proposal.steps),
                "findings": len(proposal.findings),
            },
        )
    )
