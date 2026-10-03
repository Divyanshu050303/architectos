"""The workflow controller's store, over Postgres: every read and commit in its own short transaction,
so a worker holds no transaction while a model or an engine runs. Commits go through the repository's
lease-checked commit: a workflow that was cancelled, taken over or changed meanwhile is not written."""

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.architecture_workflow.candidates import WorkflowCandidate
from core.domain.architecture_workflow.planner import Inputs
from core.domain.architecture_workflow.ports import WorkflowSnapshot
from core.domain.architecture_workflow.steps import WorkflowStep
from core.domain.architecture_workflow.workflows import ArchitectureWorkflow
from persistence.repositories.architecture_workflows import SqlAlchemyWorkflowRepository


def inputs_of(workflow: ArchitectureWorkflow) -> Inputs:
    """Which engines that need more than an architecture the goal gave inputs for."""
    goal = workflow.goal
    return Inputs(
        workload=goal.capacity_analysis_id is not None,
        pricing=goal.cost_analysis_id is not None,
        scenario=goal.scenario is not None,
    )


class PostgresWorkflowStore:
    """Implements ``WorkflowStore`` for one worker (``owner``) and its lease."""

    def __init__(
        self,
        sessions: Callable[[], AsyncSession],
        owner: str,
        *,
        lease_seconds: float,
        clock: Callable[[], datetime],
    ) -> None:
        self._sessions = sessions
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._clock = clock

    async def claim(self) -> uuid.UUID | None:
        async with self._sessions() as session, session.begin():
            return await SqlAlchemyWorkflowRepository(session).claim(
                self._owner, self._clock(), self._lease_seconds
            )

    async def release(self, workflow_id: uuid.UUID) -> None:
        async with self._sessions() as session, session.begin():
            await SqlAlchemyWorkflowRepository(session).release(self._owner, workflow_id, self._clock())

    async def snapshot(self, workflow_id: uuid.UUID) -> WorkflowSnapshot | None:
        async with self._sessions() as session, session.begin():
            found = await SqlAlchemyWorkflowRepository(session).snapshot(workflow_id)
        if found is None:
            return None
        workflow, candidates, steps = found
        return WorkflowSnapshot(workflow, candidates, steps, inputs_of(workflow))

    async def commit(
        self,
        expected: ArchitectureWorkflow,
        workflow: ArchitectureWorkflow,
        candidates: tuple[WorkflowCandidate, ...],
        step: WorkflowStep | None,
    ) -> bool:
        async with self._sessions() as session, session.begin():
            repository = SqlAlchemyWorkflowRepository(session)
            return await repository.commit(
                self._owner, expected, workflow, candidates, step, self._clock(), self._lease_seconds
            )
