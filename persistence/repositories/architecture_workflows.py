"""Architecture workflows, candidates and steps; and the workflows as a worker's queue.

Every person-facing read is scoped by project. A workflow row is also its job: a worker claims a
``queued`` workflow — or a ``running`` one whose lease expired — with ``FOR UPDATE SKIP LOCKED`` (never
two workers on one), and commits each step only while it still holds the lease and the stored workflow
is still the one it read. Stored rows are read back through the domain's own constructors.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.architecture_workflow.candidates import WorkflowCandidate
from core.domain.architecture_workflow.records import (
    candidate_document,
    candidate_from,
    step_document,
    step_from,
    workflow_document,
    workflow_from,
)
from core.domain.architecture_workflow.repository import WorkflowListing
from core.domain.architecture_workflow.steps import WorkflowStep
from core.domain.architecture_workflow.values import FailureCode, Stage, WorkflowStatus
from core.domain.architecture_workflow.workflows import ArchitectureWorkflow
from persistence.models import WorkflowCandidateRecord, WorkflowRecord, WorkflowStepRecord

W, C, S = WorkflowRecord, WorkflowCandidateRecord, WorkflowStepRecord
REQUEST = frozenset({"id", "project_id", "requested_by_user_id", "requested_at", "goal", "budget"})

type Snapshot = tuple[ArchitectureWorkflow, tuple[WorkflowCandidate, ...], tuple[WorkflowStep, ...]]


def _row(record: object, table: Any) -> dict[str, Any]:
    return {column.key: getattr(record, column.key) for column in table.__table__.columns}


def _apply(record: WorkflowRecord, workflow: ArchitectureWorkflow) -> None:
    """The workflow's new state onto its row (never its request)."""
    for name, value in workflow_document(workflow).items():
        if name not in REQUEST:
            setattr(record, name, value)


def _lease(record: WorkflowRecord, owner: str | None, until: datetime | None) -> None:
    running = record.status == WorkflowStatus.RUNNING.value
    record.lease_owner = owner if running else None
    record.lease_expires_at = until if running else None


class SqlAlchemyWorkflowRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- a person's side ---------------------------------------------------------------------------------

    async def add(self, workflow: ArchitectureWorkflow) -> ArchitectureWorkflow:
        record = WorkflowRecord(**workflow_document(workflow), lease_owner=None, lease_expires_at=None)
        self._session.add(record)
        await self._session.flush()
        return workflow

    async def get(
        self, project_id: uuid.UUID, workflow_id: uuid.UUID, *, for_update: bool = False
    ) -> ArchitectureWorkflow | None:
        statement = select(W).where(W.project_id == project_id, W.id == workflow_id)
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return workflow_from(_row(record, W)) if record else None

    async def save(self, workflow: ArchitectureWorkflow) -> ArchitectureWorkflow:
        record = await self._session.scalar(
            select(W).where(W.id == workflow.id, W.project_id == workflow.project_id).with_for_update()
        )
        if record is None:
            raise LookupError(workflow.id)  # the service read it under the same lock: never expected
        _apply(record, workflow)
        _lease(record, record.lease_owner, record.lease_expires_at)
        await self._session.flush()
        return workflow

    async def candidates(
        self, project_id: uuid.UUID, workflow_id: uuid.UUID
    ) -> tuple[WorkflowCandidate, ...]:
        statement = (
            select(C).where(C.project_id == project_id, C.workflow_id == workflow_id).order_by(C.ordinal)
        )
        return tuple(candidate_from(_row(r, C)) for r in await self._session.scalars(statement))

    async def save_candidates(self, project_id: uuid.UUID, candidates: tuple[WorkflowCandidate, ...]) -> None:
        await self._put(project_id, candidates)
        await self._session.flush()

    async def steps(self, project_id: uuid.UUID, workflow_id: uuid.UUID) -> tuple[WorkflowStep, ...]:
        statement = (
            select(S).where(S.project_id == project_id, S.workflow_id == workflow_id).order_by(S.ordinal)
        )
        return tuple(step_from(_row(r, S)) for r in await self._session.scalars(statement))

    async def _put(self, project_id: uuid.UUID, candidates: tuple[WorkflowCandidate, ...]) -> None:
        """New candidates added; existing ones get their new status and reports (nothing else changes)."""
        for candidate in candidates:
            record = await self._session.get(C, candidate.id)
            document = candidate_document(project_id, candidate)
            if record is None:
                self._session.add(C(**document))
            else:
                record.status, record.reports = document["status"], document["reports"]

    # --- a worker's side ------------------------------------------------------------------------------

    async def claim(self, owner: str, now: datetime, lease_seconds: float) -> uuid.UUID | None:
        claimable = or_(
            W.status == WorkflowStatus.QUEUED.value,
            and_(W.status == WorkflowStatus.RUNNING.value, W.lease_expires_at < now),
        )
        statement = select(W).where(claimable).order_by(W.requested_at, W.id).limit(1)
        record = await self._session.scalar(statement.with_for_update(skip_locked=True))
        if record is None:
            return None
        workflow = workflow_from(_row(record, W))
        if workflow.status is WorkflowStatus.QUEUED:
            _apply(record, workflow.start(now))
        _lease(record, owner, now + timedelta(seconds=lease_seconds))
        await self._session.flush()
        return workflow.id

    async def snapshot(self, workflow_id: uuid.UUID) -> Snapshot | None:
        record = await self._session.scalar(select(W).where(W.id == workflow_id))
        if record is None:
            return None
        workflow = workflow_from(_row(record, W))
        candidates = await self.candidates(workflow.project_id, workflow.id)
        return workflow, candidates, await self.steps(workflow.project_id, workflow.id)

    async def commit(
        self,
        owner: str,
        expected: ArchitectureWorkflow,
        workflow: ArchitectureWorkflow,
        candidates: tuple[WorkflowCandidate, ...],
        step: WorkflowStep | None,
        now: datetime,
        lease_seconds: float,
    ) -> bool:
        record = await self._session.scalar(select(W).where(W.id == expected.id).with_for_update())
        if record is None or record.lease_owner != owner or workflow_from(_row(record, W)) != expected:
            return False  # cancelled meanwhile, taken over, or changed: nothing is written
        _apply(record, workflow)
        _lease(record, owner, now + timedelta(seconds=lease_seconds))
        await self._put(workflow.project_id, candidates)
        if step is not None:
            await self._session.flush()  # the candidates a step names exist first
            self._session.add(S(**step_document(workflow.project_id, step)))
        await self._session.flush()
        return True

    async def release(self, owner: str, workflow_id: uuid.UUID, now: datetime) -> None:
        record = await self._session.scalar(select(W).where(W.id == workflow_id).with_for_update())
        if record is None or record.lease_owner != owner or record.status != WorkflowStatus.RUNNING.value:
            return
        _apply(record, workflow_from(_row(record, W)).release(now))
        _lease(record, None, None)
        await self._session.flush()

    # --- listing -------------------------------------------------------------------------------------

    async def list(
        self,
        project_id: uuid.UUID,
        *,
        status: WorkflowStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[WorkflowListing]:
        counted = (
            select(func.count())
            .where(C.workflow_id == W.id, C.project_id == W.project_id)
            .correlate(W)
            .scalar_subquery()
        )
        statement = select(
            W.id, W.goal["objective"].astext.label("objective"), W.status, W.stage, W.iteration,
            counted.label("candidates"), W.failure["code"].astext.label("failure"), W.requested_by_user_id,
            W.requested_at, W.completed_at,
        ).where(W.project_id == project_id)  # fmt: skip
        if status is not None:
            statement = statement.where(W.status == status.value)
        if after is not None:
            requested_at, workflow_id = after
            statement = statement.where(
                tuple_(W.requested_at, W.id) < tuple_(literal(requested_at), literal(workflow_id))
            )
        ordered = statement.order_by(W.requested_at.desc(), W.id.desc()).limit(limit)
        rows = await self._session.execute(ordered)
        return [
            WorkflowListing(
                row.id, row.objective, WorkflowStatus(row.status), Stage(row.stage), row.iteration,
                row.candidates, FailureCode(row.failure) if row.failure else None, row.requested_by_user_id,
                row.requested_at, row.completed_at,
            )
            for row in rows
        ]  # fmt: skip
