"""Architecture workflow use cases — everything a person does: start a workflow for a goal, read and list
workflows with their candidates and steps, give the input it waits for, cancel it, and decide its review
package: reject it, or approve one candidate as an architecture revision.

The workflow itself is carried forward by a worker (see ``workers/workflow_worker.py``), never here:
starting one only queues it. A person's move is made on the locked workflow row, so a worker holding
it can no longer commit (its expected state is gone) — a cancellation is never overwritten.

**Nothing here writes an architecture except ``approve``**, and only through the architecture
workflow (``ArchitectureService.create`` or ``replace``, source ``ai``): the person must hold
``architecture.generate`` and ``architecture.create`` (no base) or ``architecture.update`` (a revision of
the base, which must still be current: a stale base is ``stale_candidate``). The candidate approved
must be one selected for review, unchanged since (its content hash), and not blocked by validation —
not evaluated is not "no findings". The approval is recorded on the workflow and its candidate in the
revision's own transaction: all commit, or none does.

Access: starting, giving input, cancelling, rejecting and approving need ``architecture.generate``
(starting without a requirement set also ``requirement.create``: the goal is analyzed as the person);
reading needs ``architecture.read``. Every lookup goes project → workflow → candidate; another
project's or tenant's is not found. Audit entries carry identifiers, statuses and counts — never the
goal, answers or architecture content.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.domain import pagination
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.entities import Architecture
from core.domain.architecture.errors import ArchitectureVersionConflict
from core.domain.architecture.versions import ArchitectureRevision, RevisionSource
from core.domain.architecture_agent.agent_service import base_revision
from core.domain.architecture_agent.proposals import Answer
from core.domain.architecture_diff.diff_service import capacity_inputs, cost_inputs
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.requirements.analyses import validate_raw_input
from core.domain.requirements.errors import InvalidRequirementInput, RequirementSetNotFound
from core.domain.unit_of_work import UnitOfWork

from .access import goal_text
from .budget import WorkflowBudget
from .candidates import WorkflowCandidate
from .errors import (
    CandidateNotApprovable,
    InvalidWorkflowRequest,
    InvalidWorkflowTransition,
    WorkflowCandidateNotFound,
    WorkflowNotFound,
)
from .goals import WorkflowGoal
from .repository import WorkflowListing
from .steps import WorkflowStep
from .values import CandidateStatus, WorkflowStatus
from .workflows import ApprovedRevision, ArchitectureWorkflow

MAX_PAGE = 100
MAX_ANSWERS = 50
_LISTING = "architecture_workflows"


@dataclass(frozen=True, slots=True)
class WorkflowDetail:
    workflow: ArchitectureWorkflow
    candidates: tuple[WorkflowCandidate, ...]  # in ordinal order
    steps: tuple[WorkflowStep, ...]  # in execution order


@dataclass(frozen=True, slots=True)
class ApprovedCandidate:
    workflow: ArchitectureWorkflow
    candidate: WorkflowCandidate
    created_architecture: bool  # a new architecture, rather than a revision of the base


def encode_workflow_cursor(listing: WorkflowListing) -> str:
    return pagination.encode_cursor([_LISTING, listing.requested_at.isoformat(), str(listing.id)])


def decode_workflow_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    sort, requested_at, workflow_id = pagination.decode_cursor(raw, length=3)
    if sort != _LISTING:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(requested_at), uuid.UUID(workflow_id)
    except ValueError:
        raise pagination.InvalidCursor from None


def approval_blocks(candidate: WorkflowCandidate) -> str | None:
    """Why validation does not let the candidate be approved, or None. Not evaluated is not 'none'."""
    blocking = candidate.blocking
    if blocking is None:
        return "validation_unknown"
    return "validation_blocking" if blocking > 0 else None


class ArchitectureWorkflowService:
    def __init__(
        self,
        uow: UnitOfWork,
        architectures: ArchitectureService,
        *,
        budget: WorkflowBudget | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._uow = uow
        self._architectures = architectures
        self._budget = budget or WorkflowBudget()  # the configured limits; a request may only lower them
        self._clock = clock

    # --- starting --------------------------------------------------------------------------------

    async def start(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        goal: WorkflowGoal,
        limits: Mapping[str, Any] | None = None,
    ) -> ArchitectureWorkflow:
        """A queued workflow for ``goal``. Everything the goal names must exist in the project now."""
        budget = self._budget.lowered(**(limits or {}))
        if (goal.capacity_analysis_id or goal.cost_analysis_id) and goal.base is None:
            field = "capacity_analysis_id" if goal.capacity_analysis_id else "cost_analysis_id"
            raise InvalidWorkflowRequest(details={"field": field, "reason": "needs_base"})
        if goal.requirement_set_id is None:
            try:
                validate_raw_input(goal_text(goal))  # what the requirements engine will be given
            except InvalidRequirementInput:
                raise InvalidWorkflowRequest(details={"field": "objective", "reason": "too_long"}) from None
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE, lock=ProjectLock.SHARE
            )
            if goal.requirement_set_id is None:
                access.membership.require(Permission.REQUIREMENT_CREATE)  # the goal is analyzed as you
            elif await uow.requirement_sets.get(project_id, goal.requirement_set_id) is None:
                raise RequirementSetNotFound
            if goal.base is not None:
                await base_revision(uow, project_id, goal.base)
                compared = (goal.base.architecture_id, None)
                await capacity_inputs(uow, project_id, goal.capacity_analysis_id, compared)
                await cost_inputs(uow, access, goal.cost_analysis_id, compared)
            workflow = ArchitectureWorkflow(
                uuid.uuid7(), project_id, user_id, self._clock(), goal, budget,
                requirement_set_id=goal.requirement_set_id,
            )  # fmt: skip
            stored = await uow.architecture_workflows.add(workflow)
            await _audit(uow, access, AuditAction.WORKFLOW_CREATED, user_id, stored)
        return stored

    # --- reading ---------------------------------------------------------------------------------

    async def get(
        self, *, project_id: uuid.UUID, workflow_id: uuid.UUID, user_id: uuid.UUID
    ) -> WorkflowDetail:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            workflow = await _workflow(uow, project_id, workflow_id)
            candidates = await uow.architecture_workflows.candidates(project_id, workflow_id)
            steps = await uow.architecture_workflows.steps(project_id, workflow_id)
        return WorkflowDetail(workflow, candidates, steps)

    async def candidate(
        self, *, project_id: uuid.UUID, workflow_id: uuid.UUID, candidate_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[ArchitectureWorkflow, WorkflowCandidate]:
        """A candidate, with the workflow it belongs to (whose status decides whether it is approvable)."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            workflow = await _workflow(uow, project_id, workflow_id)
            candidates = await uow.architecture_workflows.candidates(project_id, workflow_id)
        return workflow, _candidate(candidates, candidate_id)

    async def list(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        status: WorkflowStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[WorkflowListing]:
        size = min(pagination.page_size(limit), MAX_PAGE)
        after = decode_workflow_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            rows = await uow.architecture_workflows.list(
                project_id, status=status, after=after, limit=size + 1
            )
        items, more = rows[:size], len(rows) > size
        return pagination.Page(items=items, next_cursor=encode_workflow_cursor(items[-1]) if more else None)

    # --- a person's input and moves --------------------------------------------------------------

    async def provide_input(
        self,
        *,
        project_id: uuid.UUID,
        workflow_id: uuid.UUID,
        user_id: uuid.UUID,
        requirement_set_id: uuid.UUID | None = None,
        answers: Sequence[tuple[str, str]] = (),
    ) -> ArchitectureWorkflow:
        """The requirement set the person confirmed, or answers (question id, text) to every blocking
        question; the workflow goes back to the queue."""
        if len(answers) > MAX_ANSWERS:
            raise InvalidWorkflowRequest(details={"field": "answers", "reason": "count"})
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE, lock=ProjectLock.SHARE
            )
            if (
                requirement_set_id is not None
                and await uow.requirement_sets.get(project_id, requirement_set_id) is None
            ):
                raise RequirementSetNotFound
            locked = await _workflow(uow, project_id, workflow_id, for_update=True)
            now = self._clock()
            given = tuple(Answer(question_id, text, user_id, now) for question_id, text in answers)
            moved = locked.provide_input(user_id, now, requirement_set_id=requirement_set_id, answers=given)
            stored = await uow.architecture_workflows.save(moved)
            facts = {"answers": len(given), "requirements_confirmed": requirement_set_id is not None}
            await _audit(uow, access, AuditAction.WORKFLOW_INPUT_PROVIDED, user_id, stored, facts)
        return stored

    async def cancel(
        self, *, project_id: uuid.UUID, workflow_id: uuid.UUID, user_id: uuid.UUID
    ) -> ArchitectureWorkflow:
        """Stops every future stage, also of a running workflow (its worker can no longer commit)."""
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE, lock=ProjectLock.SHARE
            )
            locked = await _workflow(uow, project_id, workflow_id, for_update=True)
            stored = await uow.architecture_workflows.save(locked.cancel(user_id, self._clock()))
            await _audit(uow, access, AuditAction.WORKFLOW_CANCELLED, user_id, stored)
        return stored

    async def reject(
        self, *, project_id: uuid.UUID, workflow_id: uuid.UUID, user_id: uuid.UUID, reason: str
    ) -> ArchitectureWorkflow:
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE, lock=ProjectLock.SHARE
            )
            locked = await _workflow(uow, project_id, workflow_id, for_update=True)
            stored = await uow.architecture_workflows.save(locked.reject(reason, user_id, self._clock()))
            await _audit(uow, access, AuditAction.WORKFLOW_REJECTED, user_id, stored)
        return stored

    async def approve(
        self,
        *,
        project_id: uuid.UUID,
        workflow_id: uuid.UUID,
        user_id: uuid.UUID,
        candidate_id: uuid.UUID,
        candidate_content_hash: str,
        name: str | None = None,
    ) -> ApprovedCandidate:
        """The reviewed candidate — exactly the one with ``candidate_content_hash`` — as a new
        architecture, or a revision of the goal's base, through the architecture workflow."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE)
            found = await _workflow(uow, project_id, workflow_id)
            candidate = _candidate(
                await uow.architecture_workflows.candidates(project_id, workflow_id), candidate_id
            )
        if found.status is not WorkflowStatus.REVIEW_READY:
            raise InvalidWorkflowTransition(
                details={"from": found.status.value, "to": WorkflowStatus.APPROVED.value}
            )
        if candidate.id not in found.selected or candidate.status is not CandidateStatus.SELECTED_FOR_REVIEW:
            raise CandidateNotApprovable(details={"reason": "not_selected"})
        if candidate.content_hash != candidate_content_hash:
            raise CandidateNotApprovable(details={"reason": "candidate_changed"})
        blocked = approval_blocks(candidate)
        if blocked is not None:
            raise CandidateNotApprovable(details={"reason": blocked})
        reason = f"Approved from architecture workflow {workflow_id} (candidate {candidate.ordinal})."
        recorded: list[ApprovedCandidate] = []

        async def record(uow: UnitOfWork, target: Architecture, revision: ArchitectureRevision) -> None:
            """In the revision's transaction: the approval commits with it, or neither does."""
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_GENERATE, lock=ProjectLock.SHARE
            )
            locked = await _workflow(uow, project_id, workflow_id, for_update=True)
            if locked.status is not WorkflowStatus.REVIEW_READY:  # decided or cancelled meanwhile
                moved = {"from": locked.status.value, "to": WorkflowStatus.APPROVED.value}
                raise InvalidWorkflowTransition(details=moved)
            now = self._clock()
            approved = locked.approve(
                ApprovedRevision(candidate.id, target.id, revision.number), user_id, now
            )
            stored = await uow.architecture_workflows.save(approved)
            current = _candidate(
                await uow.architecture_workflows.candidates(project_id, workflow_id), candidate.id
            )
            accepted = current.moved(CandidateStatus.ACCEPTED)
            await uow.architecture_workflows.save_candidates(project_id, (accepted,))
            facts = {
                "candidate_id": str(candidate.id),
                "architecture_id": str(target.id),
                "revision": revision.number,
            }
            await _audit(uow, access, AuditAction.WORKFLOW_APPROVED, user_id, stored, facts)
            recorded.append(ApprovedCandidate(stored, accepted, found.goal.base is None))

        base, set_id = found.goal.base, found.requirement_set_id
        if base is None:
            await self._architectures.create(
                project_id=project_id, user_id=user_id, name=name or candidate.ir.name, ir=candidate.ir,
                source=RevisionSource.AI, reason=reason, requirement_set_id=set_id, then=record,
            )  # fmt: skip
        else:
            try:
                revised = await self._architectures.replace(
                    project_id=project_id, architecture_id=base.architecture_id, user_id=user_id,
                    base_version=base.number, ir=candidate.ir, source=RevisionSource.AI, reason=reason,
                    requirement_set_id=set_id, then=record,
                )  # fmt: skip
            except ArchitectureVersionConflict as conflict:  # the base moved on since the workflow read it
                details = {"reason": "stale_candidate"} | dict(conflict.details or {})
                raise CandidateNotApprovable(details=details) from None
            if not revised.created:  # the candidate is the base's own content: nothing was approved
                raise CandidateNotApprovable(details={"reason": "no_change"})
        return recorded[0]


async def _workflow(
    uow: UnitOfWork, project_id: uuid.UUID, workflow_id: uuid.UUID, *, for_update: bool = False
) -> ArchitectureWorkflow:
    found = await uow.architecture_workflows.get(project_id, workflow_id, for_update=for_update)
    if found is None:
        raise WorkflowNotFound
    return found


def _candidate(candidates: tuple[WorkflowCandidate, ...], candidate_id: uuid.UUID) -> WorkflowCandidate:
    found = next((c for c in candidates if c.id == candidate_id), None)
    if found is None:
        raise WorkflowCandidateNotFound
    return found


async def _audit(
    uow: UnitOfWork,
    access: ProjectAccess,
    action: AuditAction,
    user_id: uuid.UUID,
    workflow: ArchitectureWorkflow,
    extra: dict[str, Any] | None = None,
) -> None:
    """Identifiers, statuses and counts only."""
    metadata: dict[str, Any] = {
        "project_id": str(workflow.project_id),
        "status": workflow.status.value,
        "stage": workflow.stage.value,
        "iteration": workflow.iteration,
        "requirement_set_id": str(workflow.requirement_set_id) if workflow.requirement_set_id else None,
        "base_architecture_id": str(workflow.goal.base.architecture_id) if workflow.goal.base else None,
    }
    if workflow.failure is not None:
        metadata["failure"] = workflow.failure.code.value
    await uow.audit.record(
        AuditEvent(
            action,
            actor_user_id=user_id,
            organization_id=access.project.organization_id,
            resource_type="architecture_workflow",
            resource_id=workflow.id,
            metadata=metadata | (extra or {}),
        )
    )
