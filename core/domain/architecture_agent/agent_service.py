"""Architecture agent use cases: start a run, answer its questions, cancel it, read and list runs, and
— a person's decision — accept its candidate as an architecture revision or reject it.

A pass is run in three steps like the analyses: authorize and load (a short transaction: the pinned
requirement set, its requirement versions, the base revision, the policy), run the pipeline (no
transaction: knowledge is read through the retriever, which authorizes on its own, and the model is
called), then store — re-authorized under the project lock — and audit. Nothing is stored before the
pass ends; a run waiting for answers is stored as such, and answering it runs the next pass.

**Nothing here writes an architecture except ``accept``**, and only through the architecture
workflow (``ArchitectureService.create`` or ``replace``, source ``ai``): the person must hold
``architecture.generate`` and ``architecture.create`` (a new architecture) or ``architecture.update``
(a revision of the base, which must still be current: a stale base is a conflict). The candidate
accepted must be the one reviewed (its content hash) and validation must not block it. The
acceptance is recorded on the run in the revision's own transaction: both commit, or neither does.

Access: starting, answering, cancelling, accepting and rejecting need ``architecture.generate``;
reading needs ``architecture.read``. Every lookup goes project → run; a run of another project or
tenant is not found. Audit entries carry identifiers, statuses and counts — never the objective,
answers, prompts, passages or model output.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from core.architecture_ir.model import ArchitectureIR
from core.domain import pagination
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.entities import Architecture
from core.domain.architecture.errors import ArchitectureNotFound, ArchitectureRevisionNotFound
from core.domain.architecture.versions import ArchitectureRevision, RevisionSource
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.knowledge.ports import KnowledgeRetriever
from core.domain.knowledge.retrieval import RetrievalQuery, RetrievalResult
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.requirements.entities import Requirement
from core.domain.requirements.errors import RequirementSetNotFound
from core.domain.requirements.planning import PlanningInputV2
from core.domain.unit_of_work import UnitOfWork

from .errors import AgentRunNotFound, CandidateNotAcceptable, InvalidAgentRequest, InvalidAgentTransition
from .ports import AgentPipeline, PassInputs
from .proposals import Answer
from .repository import RunListing
from .requests import AgentRequest, Budget
from .runs import AcceptedRevision, AgentRun
from .values import EngineStatus, RunStatus

MAX_PAGE = 100
MAX_ANSWERS = 50
MAX_REASON = 2000
_LISTING = "agent_runs"


@dataclass(frozen=True, slots=True)
class AcceptedCandidate:
    run: AgentRun
    created_architecture: bool  # a new architecture, rather than a revision of the base


@dataclass(frozen=True, slots=True)
class _Loaded:
    planning_input: PlanningInputV2
    requirements: tuple[Requirement, ...]
    policy: ArchitecturePolicy
    base: ArchitectureIR | None
    missing: tuple[str, ...]  # pinned requirements that can no longer be read


def encode_run_cursor(listing: RunListing) -> str:
    return pagination.encode_cursor([_LISTING, listing.requested_at.isoformat(), str(listing.id)])


def decode_run_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    sort, requested_at, run_id = pagination.decode_cursor(raw, length=3)
    if sort != _LISTING:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(requested_at), uuid.UUID(run_id)
    except ValueError:
        raise pagination.InvalidCursor from None


def validation_blocks(run: AgentRun) -> str | None:
    """Why validation does not let the candidate be accepted, or None. Not evaluated is not 'none'."""
    for report in run.reports:
        if report.engine == "validation" and report.status is EngineStatus.EVALUATED:
            blocking = report.summary.get("blocking")
            if not isinstance(blocking, int) or isinstance(blocking, bool):
                return "validation_unknown"
            return "validation_blocking" if blocking > 0 else None
    return "validation_missing"


class ArchitectureAgentService:
    def __init__(
        self,
        uow: UnitOfWork,
        pipeline: AgentPipeline,
        retriever: KnowledgeRetriever,
        architectures: ArchitectureService,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._uow = uow
        self._pipeline = pipeline
        self._retriever = retriever
        self._architectures = architectures
        self._clock = clock

    # --- running ---------------------------------------------------------------------------------

    async def start(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        request: AgentRequest,
        budget: Budget | None = None,
    ) -> AgentRun:
        """A new run: one pass, stored as it ended (waiting for answers, a candidate, or failed)."""
        run = AgentRun(
            id=uuid.uuid7(),
            project_id=project_id,
            requested_by_user_id=user_id,
            requested_at=self._clock(),
            request=request,
            budget=budget or Budget(),
        )
        advanced = await self._advance(run, await self._load(project_id, user_id, request), user_id)
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE, lock=ProjectLock.SHARE
            )
            stored = await uow.agent_runs.add(advanced)
            await _audit(uow, access, AuditAction.AGENT_RUN_CREATED, user_id, stored)
        return stored

    async def answer(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        answers: Sequence[tuple[str, str]],
    ) -> AgentRun:
        """Answers (question id, text) to the run's questions; once every blocking one is answered,
        the same run continues, with them as the person's statements."""
        if not answers or len(answers) > MAX_ANSWERS:
            raise InvalidAgentRequest(details={"field": "answers", "reason": "count"})
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE)
            found = await _run(uow, project_id, run_id)
        now = self._clock()
        given = tuple(Answer(question_id, text, user_id, now) for question_id, text in answers)
        resumed = found.answer(given, user_id, now)  # refuses unknown questions and missing answers
        advanced = await self._advance(resumed, await self._load(project_id, user_id, found.request), user_id)
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE, lock=ProjectLock.SHARE
            )
            locked = await _run(uow, project_id, run_id, for_update=True)
            if locked.status is not RunStatus.AWAITING_CLARIFICATION or locked.answers != found.answers:
                moved = {"from": locked.status.value, "to": RunStatus.RUNNING.value}
                raise InvalidAgentTransition(details=moved)  # answered or cancelled meanwhile
            stored = await uow.agent_runs.save(advanced)
            facts = {"answers": len(given)}
            await _audit(uow, access, AuditAction.AGENT_RUN_ANSWERED, user_id, stored, facts)
        return stored

    async def cancel(self, *, project_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID) -> AgentRun:
        """A run waiting for answers, abandoned (a stored run never waits in any other state)."""
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE, lock=ProjectLock.SHARE
            )
            locked = await _run(uow, project_id, run_id, for_update=True)
            stored = await uow.agent_runs.save(locked.cancel(user_id, self._clock()))
            await _audit(uow, access, AuditAction.AGENT_RUN_CANCELLED, user_id, stored)
        return stored

    # --- the person's decision -------------------------------------------------------------------

    async def reject(
        self, *, project_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID, reason: str
    ) -> AgentRun:
        clean = " ".join(reason.split()) if isinstance(reason, str) else ""
        if not clean or len(clean) > MAX_REASON:
            problem = "required" if not clean else "too_long"
            raise InvalidAgentRequest(details={"field": "reason", "reason": problem})
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE, lock=ProjectLock.SHARE
            )
            locked = await _run(uow, project_id, run_id, for_update=True)
            stored = await uow.agent_runs.save(locked.reject(clean, user_id, self._clock()))
            await _audit(uow, access, AuditAction.AGENT_RUN_REJECTED, user_id, stored)
        return stored

    async def accept(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        candidate_content_hash: str,
        name: str | None = None,
    ) -> AcceptedCandidate:
        """The reviewed candidate — exactly the one with ``candidate_content_hash`` — as a new
        architecture, or (an iteration) a revision of the base, through the architecture workflow."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE)
            found = await _run(uow, project_id, run_id)
        candidate = found.candidate
        if found.status is not RunStatus.CANDIDATE_READY or candidate is None:
            raise InvalidAgentTransition(details={"from": found.status.value, "to": RunStatus.ACCEPTED.value})
        if candidate.content_hash != candidate_content_hash:
            raise CandidateNotAcceptable(details={"reason": "candidate_changed"})
        blocked = validation_blocks(found)
        if blocked is not None:
            raise CandidateNotAcceptable(details={"reason": blocked})
        reason = f"Accepted from architecture agent run {run_id}."
        recorded: list[AgentRun] = []

        async def record(uow: UnitOfWork, target: Architecture, revision: ArchitectureRevision) -> None:
            """In the revision's transaction: the acceptance commits with it, or neither does."""
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE, lock=ProjectLock.SHARE
            )
            locked = await _run(uow, project_id, run_id, for_update=True)
            if locked.status is not RunStatus.CANDIDATE_READY:  # decided meanwhile
                moved = {"from": locked.status.value, "to": RunStatus.ACCEPTED.value}
                raise InvalidAgentTransition(details=moved)
            made = AcceptedRevision(target.id, revision.number, revision.content_hash)
            stored = await uow.agent_runs.save(locked.accept(made, user_id, self._clock()))
            facts = {"architecture_id": str(target.id), "revision": revision.number}
            await _audit(uow, access, AuditAction.AGENT_RUN_ACCEPTED, user_id, stored, facts)
            recorded.append(stored)

        base, set_id = found.request.base, found.request.requirement_set_id
        if base is None:
            await self._architectures.create(
                project_id=project_id, user_id=user_id, name=name or candidate.ir.name, ir=candidate.ir,
                source=RevisionSource.AI, reason=reason, requirement_set_id=set_id, then=record,
            )  # fmt: skip
        else:
            revised = await self._architectures.replace(
                project_id=project_id, architecture_id=base.architecture_id, user_id=user_id,
                base_version=base.number, ir=candidate.ir, source=RevisionSource.AI, reason=reason,
                requirement_set_id=set_id, then=record,
            )  # fmt: skip
            if not revised.created:  # the candidate is the base's own content: nothing was accepted
                raise CandidateNotAcceptable(details={"reason": "no_change"})
        return AcceptedCandidate(recorded[0], base is None)

    # --- reading ---------------------------------------------------------------------------------

    async def get(self, *, project_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID) -> AgentRun:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            return await _run(uow, project_id, run_id)

    async def list(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        status: RunStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[RunListing]:
        size = min(pagination.page_size(limit), MAX_PAGE)
        after = decode_run_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            rows = await uow.agent_runs.list(project_id, status=status, after=after, limit=size + 1)
        items, more = rows[:size], len(rows) > size
        return pagination.Page(items=items, next_cursor=encode_run_cursor(items[-1]) if more else None)

    # --- passes ----------------------------------------------------------------------------------

    async def _load(self, project_id: uuid.UUID, user_id: uuid.UUID, request: AgentRequest) -> _Loaded:
        async with self._uow as uow:
            access = await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE)
            found = await uow.requirement_sets.get(project_id, request.requirement_set_id)
            planned = await uow.requirement_sets.get_planning_input(project_id, request.requirement_set_id)
            if found is None or planned is None:
                raise RequirementSetNotFound
            requirements: list[Requirement] = []
            missing: list[str] = []
            for item in found.items:
                version = await uow.requirements.get_version(project_id, item.requirement_id, item.version)
                if version is None:
                    missing.append(item.reference)
                    continue
                requirements.append(
                    Requirement(
                        id=item.requirement_id, project_id=project_id, number=item.number,
                        version=version.version, content=version.content, source=version.source,
                        confidence=version.confidence, created_by_user_id=version.created_by_user_id,
                        created_at=version.created_at, updated_at=version.created_at,
                    )
                )  # fmt: skip
            base = await _base(uow, project_id, request) if request.base is not None else None
        planning_input = cast(PlanningInputV2, planned[1])
        policy = access.project.policy
        return _Loaded(planning_input, tuple(requirements), policy, base, tuple(missing))

    async def _advance(self, run: AgentRun, loaded: _Loaded, user_id: uuid.UUID) -> AgentRun:
        project_id = run.project_id

        async def retrieve(query: RetrievalQuery) -> RetrievalResult:
            return await self._retriever.retrieve(project_id=project_id, user_id=user_id, query=query)

        if loaded.missing:
            gone = ", ".join(loaded.missing)
            run = run.noting(
                f"Pinned requirement(s) {gone} can no longer be read; the engines ran without them."
            )
        inputs = PassInputs(loaded.planning_input, loaded.requirements, loaded.policy, retrieve, loaded.base)
        return await self._pipeline.advance(run, inputs)


async def _base(uow: UnitOfWork, project_id: uuid.UUID, request: AgentRequest) -> ArchitectureIR:
    """The exact revision an iteration starts from — of an architecture of this project."""
    assert request.base is not None  # noqa: S101 - checked by the caller
    architecture = await uow.architectures.get(project_id, request.base.architecture_id)
    if architecture is None:
        raise ArchitectureNotFound
    revision = await uow.architectures.get_revision(architecture.id, request.base.number)
    if revision is None:
        raise ArchitectureRevisionNotFound
    return revision.ir


async def _run(
    uow: UnitOfWork, project_id: uuid.UUID, run_id: uuid.UUID, *, for_update: bool = False
) -> AgentRun:
    found = await uow.agent_runs.get(project_id, run_id, for_update=for_update)
    if found is None:
        raise AgentRunNotFound
    return found


async def _audit(
    uow: UnitOfWork,
    access: ProjectAccess,
    action: AuditAction,
    user_id: uuid.UUID,
    run: AgentRun,
    extra: dict[str, Any] | None = None,
) -> None:
    """Identifiers, statuses and counts only."""
    metadata: dict[str, Any] = {
        "project_id": str(run.project_id),
        "requirement_set_id": str(run.request.requirement_set_id),
        "status": run.status.value,
        "stage": run.stage.value,
        "model_calls": run.usage.model_calls,
        "questions": len(run.questions),
    }
    if run.failure is not None:
        metadata["failure"] = run.failure.code.value
    if run.prompt_version is not None:
        metadata["prompt_version"] = run.prompt_version
    await uow.audit.record(
        AuditEvent(
            action,
            actor_user_id=user_id,
            organization_id=access.project.organization_id,
            resource_type="agent_run",
            resource_id=run.id,
            metadata=metadata | (extra or {}),
        )
    )
