"""Discovery use cases: run a discovery over supplied artifacts, list and read runs (with their findings
and proposal), record a person's decision about a candidate, accept the reviewed proposal as an
architecture or a new revision, compare runs (and a run with a baseline revision), delete a run.

Running is synchronous, in three steps like the analyses: authorize and read the baseline (a short
transaction), discover (on a worker thread, no transaction: the request is immutable and nothing is
fetched or executed), then store — re-authorized under the project lock — and audit. A run the engine
refuses for its limits is stored as ``failed``, with the reason.

**Nothing here writes an architecture except ``accept``**, and only through the architecture
workflow (``ArchitectureService.create`` or ``replace``, source ``discovery``): the person must hold
``architecture.discover`` and ``architecture.create`` (a new architecture) or ``architecture.update``
(a new revision on ``base_version``, which must still be current); the proposal accepted must be the
one reviewed (its content hash). History is never rewritten. The acceptance is recorded on the run in
the revision's own transaction: both commit, or neither does.

Access: running, deciding, accepting and deleting need ``architecture.discover``; reading needs
``architecture.read``. Every lookup goes project -> run; a run of another project or tenant is not
found. Audit entries carry identifiers and counts only — never artifact content or values.
"""

import asyncio
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain import pagination
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.entities import Architecture
from core.domain.architecture.errors import ArchitectureNotFound, ArchitectureRevisionNotFound
from core.domain.architecture.versions import ArchitectureRevision, RevisionSource
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.unit_of_work import UnitOfWork

from .comparison import BaselineComparison, ResultComparison, compare_results, compare_with_baseline
from .errors import (
    AcceptedRunNotDeleted,
    DiscoveryRunNotFound,
    InvalidDiscoveryRequest,
    InvalidDiscoveryResult,
    InvalidDiscoveryTransition,
    ProposalNotAcceptable,
)
from .findings import Finding
from .ports import DiscoveryEngine
from .results import DiscoveryResult, Proposal
from .runs import (
    Acceptance,
    Baseline,
    DiscoveryRequest,
    DiscoveryRun,
    ReviewDecision,
    RunError,
    RunListing,
)
from .values import RunStatus

MAX_PAGE = 100
MAX_RESULT_BYTES = 32 * 1024 * 1024  # a stored result (JSON); beyond it the run fails, stating why
_LISTING = "discovery_runs"


@dataclass(frozen=True, slots=True)
class AcceptedProposal:
    run: DiscoveryRun
    acceptance: Acceptance
    created_architecture: bool  # a new architecture, rather than a revision of an existing one
    created_revision: bool  # False: the content was already the architecture's current content


def encode_run_cursor(requested_at: datetime, run_id: uuid.UUID) -> str:
    return pagination.encode_cursor([_LISTING, requested_at.isoformat(), str(run_id)])


def decode_run_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    sort, requested_at, run_id = pagination.decode_cursor(raw, length=3)
    if sort != _LISTING:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(requested_at), uuid.UUID(run_id)
    except ValueError:
        raise pagination.InvalidCursor from None


def _result_bytes(result: DiscoveryResult) -> int:
    return len(json.dumps(result.to_dict(), default=str).encode())


class DiscoveryService:
    def __init__(self, uow: UnitOfWork, engine: DiscoveryEngine, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._engine = engine
        self._clock = clock
        self._architectures = ArchitectureService(uow, clock=clock)

    # --- running ---------------------------------------------------------------------------------

    async def run(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, request: DiscoveryRequest
    ) -> DiscoveryRun:
        """A discovery of the request's artifacts, stored completed (with or without warnings) or
        failed. The architecture — and the baseline — are never changed."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_DISCOVER)
            if request.baseline is not None:
                await _baseline(uow, project_id, request.baseline)
        requested_at = self._clock()
        run = DiscoveryRun(
            uuid.uuid7(), project_id, RunStatus.PENDING, user_id, requested_at,
            request.source_type, request.baseline, request.label,
        ).start(requested_at)  # fmt: skip
        try:
            result = await asyncio.to_thread(self._engine.discover, request)
            if _result_bytes(result) > MAX_RESULT_BYTES:
                raise InvalidDiscoveryRequest(details={"field": "artifacts", "reason": "too_large_result"})
            run = run.finish(result, self._clock())
        except (InvalidDiscoveryRequest, InvalidDiscoveryResult) as error:
            reason = str((error.details or {}).get("reason") or error.code)
            run = run.fail(RunError(reason, error.message), self._clock())
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_DISCOVER, lock=ProjectLock.SHARE
            )
            await uow.discoveries.add(run)
            await _audit(uow, access, AuditAction.DISCOVERY_RUN_CREATED, user_id, run)
        return run

    # --- reading ---------------------------------------------------------------------------------

    async def get(self, *, project_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID) -> DiscoveryRun:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            return await _run(uow, project_id, run_id)

    async def list_runs(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        status: RunStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[RunListing]:
        """The project's runs, newest first, without their results."""
        size = max(1, min(limit, MAX_PAGE))
        after = decode_run_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            rows = await uow.discoveries.list(project_id, status=status, after=after, limit=size + 1)
        page, more = rows[:size], len(rows) > size
        last = page[-1] if more and page else None
        return pagination.Page(
            items=page, next_cursor=encode_run_cursor(last.requested_at, last.id) if last else None
        )

    async def findings(
        self, *, project_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID, entity: str | None = None
    ) -> tuple[Finding, ...]:
        """The run's findings (optionally of one entity) — each value as written, secrets redacted."""
        found = await self.get(project_id=project_id, run_id=run_id, user_id=user_id)
        findings = found.result.findings if found.result else ()
        return tuple(f for f in findings if entity is None or f.entity == entity)

    async def proposal(
        self, *, project_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID, name: str | None = None
    ) -> tuple[DiscoveryRun, Proposal]:
        """The proposal under the run's review decisions, computed now from the stored result."""
        found = await self.get(project_id=project_id, run_id=run_id, user_id=user_id)
        return found, await self._propose(found, name)

    async def _propose(self, run: DiscoveryRun, name: str | None = None) -> Proposal:
        if run.result is None:
            raise InvalidDiscoveryTransition(details={"from": run.status.value, "to": "proposed"})
        result, decisions = run.result, run.decisions
        return await asyncio.to_thread(self._engine.propose, result, decisions, name or run.label)

    # --- review ----------------------------------------------------------------------------------

    async def decide(
        self, *, project_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID, decision: ReviewDecision
    ) -> DiscoveryRun:
        """A person's decision about one candidate (history: the latest per candidate applies)."""
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_DISCOVER, lock=ProjectLock.SHARE
            )
            found = await _run(uow, project_id, run_id, for_update=True)
            decided = found.decide(decision)
            await uow.discoveries.update_review(decided)
            facts = {"subject_type": decision.subject_type.value, "decision": decision.decision.value}
            await _audit(uow, access, AuditAction.DISCOVERY_RUN_REVIEWED, user_id, decided, facts)
        return decided

    async def accept(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        proposal_content_hash: str,
        architecture_id: uuid.UUID | None = None,
        base_version: int | None = None,
        name: str | None = None,
    ) -> AcceptedProposal:
        """The reviewed proposal — exactly the one with ``proposal_content_hash`` — as a new architecture
        (``architecture_id`` None) or a new revision of ``architecture_id`` based on ``base_version``,
        through the architecture workflow. Nothing is overwritten: a stale base is a conflict."""
        if architecture_id is not None and base_version is None:
            raise InvalidDiscoveryRequest(details={"field": "base_version", "reason": "required"})
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_DISCOVER)
            found = await _run(uow, project_id, run_id)
        proposal = await self._propose(found)  # exactly as reviewed (GET .../proposal)
        architecture = proposal.architecture
        problem = proposal.acceptance_problem
        if (
            problem is None
            and architecture is not None
            and content_hash(architecture) != proposal_content_hash
        ):
            problem = "proposal_changed"  # the decisions changed since it was reviewed
        if problem is not None or architecture is None:
            raise ProposalNotAcceptable(details={"reason": problem or "nothing_to_accept"})
        reason = f"Accepted from discovery run {run_id}."
        recorded: list[DiscoveryRun] = []

        async def record(uow: UnitOfWork, target: Architecture, revision: ArchitectureRevision) -> None:
            """In the revision's transaction: the acceptance commits with it, or neither does."""
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_DISCOVER, lock=ProjectLock.SHARE
            )
            locked = await _run(uow, project_id, run_id, for_update=True)
            if locked.decisions != found.decisions:  # reviewed again meanwhile: not what was accepted
                raise ProposalNotAcceptable(details={"reason": "proposal_changed"})
            acceptance = Acceptance(target.id, revision.number, revision.content_hash, user_id, self._clock())
            accepted = locked.accepted(acceptance)
            await uow.discoveries.update_review(accepted)
            facts = {
                "architecture_id": str(target.id),
                "revision": revision.number,
                "created_architecture": architecture_id is None,
            }
            await _audit(uow, access, AuditAction.DISCOVERY_RUN_ACCEPTED, user_id, accepted, facts)
            recorded.append(accepted)

        if architecture_id is None or base_version is None:
            await self._architectures.create(
                project_id=project_id, user_id=user_id, name=name or architecture.name, ir=architecture,
                source=RevisionSource.DISCOVERY, reason=reason, then=record,
            )  # fmt: skip
            made_revision = True
        else:
            revised = await self._architectures.replace(
                project_id=project_id, architecture_id=architecture_id, user_id=user_id,
                base_version=base_version, ir=architecture, source=RevisionSource.DISCOVERY, reason=reason,
                then=record,
            )  # fmt: skip
            made_revision = revised.created
        run = recorded[0]
        return AcceptedProposal(run, run.acceptances[-1], architecture_id is None, made_revision)

    # --- comparison ------------------------------------------------------------------------------

    async def compare(
        self, *, project_id: uuid.UUID, run_id: uuid.UUID, other_run_id: uuid.UUID, user_id: uuid.UUID
    ) -> ResultComparison:
        """The later of two runs against the earlier, within what both read alike."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            first, second = await _run(uow, project_id, run_id), await _run(uow, project_id, other_run_id)
        earlier, later = sorted([first, second], key=lambda r: (r.requested_at, r.id))
        if earlier.result is None or later.result is None:
            failed = earlier if earlier.result is None else later
            raise InvalidDiscoveryTransition(details={"from": failed.status.value, "to": "compared"})
        return compare_results(earlier.result, later.result)

    async def compare_with_baseline(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        baseline: Baseline | None = None,
    ) -> BaselineComparison:
        """The run's reviewed proposal against a baseline revision (default: the run's baseline)."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            found = await _run(uow, project_id, run_id)
            chosen = baseline or found.baseline
            if chosen is None:
                raise InvalidDiscoveryRequest(details={"field": "baseline", "reason": "required"})
            reference = await _baseline(uow, project_id, chosen)
        proposal = await self._propose(found)
        if found.result is None:  # refused by _propose already
            raise InvalidDiscoveryTransition(details={"from": found.status.value, "to": "compared"})
        proposed = proposal.architecture or ArchitectureIR(reference.name)
        return compare_with_baseline(found.result, proposed, reference)

    # --- retention -------------------------------------------------------------------------------

    async def delete(self, *, project_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Removes a run and its findings. A run whose proposal was accepted is kept: it is the
        provenance of the revision it created."""
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_DISCOVER, lock=ProjectLock.SHARE
            )
            found = await _run(uow, project_id, run_id, for_update=True)
            if found.acceptances:
                raise AcceptedRunNotDeleted
            await uow.discoveries.delete(project_id, run_id)
            await _audit(uow, access, AuditAction.DISCOVERY_RUN_DELETED, user_id, found)


async def _run(
    uow: UnitOfWork, project_id: uuid.UUID, run_id: uuid.UUID, *, for_update: bool = False
) -> DiscoveryRun:
    found = await uow.discoveries.get(project_id, run_id, for_update=for_update)
    if found is None:
        raise DiscoveryRunNotFound
    return found


async def _baseline(uow: UnitOfWork, project_id: uuid.UUID, baseline: Baseline) -> ArchitectureIR:
    """The baseline revision's content — of an architecture of this project."""
    architecture = await uow.architectures.get(project_id, baseline.architecture_id)
    if architecture is None:
        raise ArchitectureNotFound
    revision = await uow.architectures.get_revision(architecture.id, baseline.revision_number)
    if revision is None:
        raise ArchitectureRevisionNotFound
    return revision.ir


async def _audit(
    uow: UnitOfWork,
    access: ProjectAccess,
    action: AuditAction,
    user_id: uuid.UUID,
    run: DiscoveryRun,
    extra: Mapping[str, object] | None = None,
) -> None:
    result = run.result
    metadata: dict[str, object] = {
        "project_id": str(run.project_id),
        "run_id": str(run.id),
        "status": run.status.value,
        "artifacts": len(result.artifacts) if result else 0,
        "entities": len(result.entities) if result else 0,
        "relationships": len(result.relationships) if result else 0,
        "decisions": len(run.decisions),
        "acceptances": len(run.acceptances),
    }
    await uow.audit.record(
        AuditEvent(
            action,
            actor_user_id=user_id,
            organization_id=access.project.organization_id,
            resource_type="discovery_run",
            resource_id=run.id,
            metadata=metadata | dict(extra or {}),
        )
    )
