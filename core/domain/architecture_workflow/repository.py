import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .candidates import WorkflowCandidate
from .steps import WorkflowStep
from .values import FailureCode, Stage, WorkflowStatus
from .workflows import ArchitectureWorkflow


@dataclass(frozen=True, slots=True)
class WorkflowListing:
    """A workflow in a project's list: what it is and where it stands, without its parts."""

    id: uuid.UUID
    objective: str
    status: WorkflowStatus
    stage: Stage
    iteration: int
    candidates: int
    failure: FailureCode | None
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    completed_at: datetime | None


class WorkflowRepository(Protocol):
    """A project's workflows, candidates and steps; every read is scoped by project. A workflow's
    request never changes; a finished workflow never changes; candidates keep their architecture and
    lineage; steps are append-only. None is deleted."""

    async def add(self, workflow: ArchitectureWorkflow) -> ArchitectureWorkflow: ...

    async def get(
        self, project_id: uuid.UUID, workflow_id: uuid.UUID, *, for_update: bool = False
    ) -> ArchitectureWorkflow | None: ...

    async def save(self, workflow: ArchitectureWorkflow) -> ArchitectureWorkflow:
        """A person's move (input, cancel, approve, reject): the new state; any lease is released."""
        ...

    async def candidates(
        self, project_id: uuid.UUID, workflow_id: uuid.UUID
    ) -> tuple[WorkflowCandidate, ...]:
        """In ordinal order."""
        ...

    async def save_candidates(self, project_id: uuid.UUID, candidates: tuple[WorkflowCandidate, ...]) -> None:
        """New status of existing candidates (e.g. accepted); their architecture never changes."""
        ...

    async def steps(self, project_id: uuid.UUID, workflow_id: uuid.UUID) -> tuple[WorkflowStep, ...]:
        """In execution order."""
        ...

    async def list(
        self,
        project_id: uuid.UUID,
        *,
        status: WorkflowStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[WorkflowListing]:
        """The project's workflows, newest first."""
        ...


class WorkflowJobs(Protocol):
    """The workflows as a worker's queue: claimed under a lease, committed only while it holds it."""

    async def claim(self, owner: str, now: datetime, lease_seconds: float) -> uuid.UUID | None:
        """A queued workflow (started) or a running one whose lease expired (resumed), now leased to
        ``owner`` — or None. Never two workers at once (rows are locked and skipped)."""
        ...

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
        """The workflow's new state with its new or changed candidates and its step, all or nothing —
        only while ``owner`` holds the lease and the stored workflow is still ``expected``. A running
        workflow's lease is extended; any other's is released."""
        ...

    async def release(self, owner: str, workflow_id: uuid.UUID, now: datetime) -> None:
        """Back to the queue, if ``owner`` still holds it (e.g. the worker is stopping)."""
        ...
