"""Architecture workflows in the database (Autonomous Architecture Workflow, phase 4): a worker claims a
workflow under a lease and carries it to review, each step committed with its effects and read back as it
was; a lease keeps other workers out until it expires, then another resumes it and the first can no longer
write; a person's cancellation is never overwritten by a worker; the records are append-only where they
must be."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from core.domain.architecture_workflow.budget import WorkflowBudget
from core.domain.architecture_workflow.controller import WorkflowController
from core.domain.architecture_workflow.goals import WorkflowGoal
from core.domain.architecture_workflow.values import CandidateStatus, StepStatus, WorkflowStatus
from core.domain.architecture_workflow.workflows import ArchitectureWorkflow
from persistence.repositories.architecture_workflows import SqlAlchemyWorkflowRepository
from persistence.workflow_store import PostgresWorkflowStore
from tests.integration.conftest import joined_session
from tests.unit.architecture_workflow.test_workflow_controller import Permissions, Script
from workers.workflow_worker import WorkflowWorker

from .requirement_support import World
from .test_architecture_agent import requirement_set

pytestmark = pytest.mark.integration

AT = datetime(2026, 10, 3, 12, tzinfo=UTC)
USER = uuid.uuid4()
DUPLICATE_ATTEMPT = """
    INSERT INTO workflow_steps (id, workflow_id, project_id, key, attempt, ordinal, iteration, action, stage,
        status, started_at, completed_at, subject, candidate_id, outputs, usage, error, retryable, note)
    SELECT gen_random_uuid(), workflow_id, project_id, key, attempt, ordinal + 100, iteration, action, stage,
        status, started_at, completed_at, subject, candidate_id, outputs, usage, error, retryable, note
    FROM workflow_steps LIMIT 1
"""  # a fresh row id, the same operation key and attempt


class Clock:
    def __init__(self, at: datetime = AT) -> None:
        self.now = at

    def __call__(self) -> datetime:
        return self.now


async def queued(client: AsyncClient, db: AsyncSession, world: World, **budget: Any) -> ArchitectureWorkflow:
    set_id = uuid.UUID(await requirement_set(client, world))
    goal = WorkflowGoal("An order service for a web shop", requirement_set_id=set_id)
    workflow = ArchitectureWorkflow(
        uuid.uuid4(),
        uuid.UUID(world.project_id),
        USER,
        AT,
        goal,
        WorkflowBudget(**budget),
        requirement_set_id=set_id,
    )
    async with db.begin_nested():
        await SqlAlchemyWorkflowRepository(db).add(workflow)
    return workflow


def store(connection: AsyncConnection, owner: str, clock: Clock, lease: float = 60) -> PostgresWorkflowStore:
    return PostgresWorkflowStore(lambda: joined_session(connection), owner, lease_seconds=lease, clock=clock)


def worker(queue: PostgresWorkflowStore, script: Script, clock: Clock, turns: int = 50) -> WorkflowWorker:
    controller = WorkflowController(queue, script.executors(), Permissions(), clock=clock)
    return WorkflowWorker(queue, controller, turns=turns)


async def test_a_worker_carries_a_workflow_to_review_and_everything_is_stored(
    client: AsyncClient, db: AsyncSession, connection: AsyncConnection, world: World
) -> None:
    flow = await queued(client, db, world)
    clock = Clock()
    claimed = await worker(store(connection, "worker-1", clock), Script(), clock).run_once()
    assert claimed == flow.id
    repository = SqlAlchemyWorkflowRepository(db)
    project = uuid.UUID(world.project_id)
    stored = await repository.get(project, flow.id)
    assert stored is not None
    assert stored.status is WorkflowStatus.REVIEW_READY
    candidates = await repository.candidates(project, flow.id)
    steps = await repository.steps(project, flow.id)
    assert [c.ordinal for c in candidates] == [1, 2]
    assert candidates[1].parent_id == candidates[0].id
    assert {c.status for c in candidates} == {CandidateStatus.SELECTED_FOR_REVIEW}
    assert set(stored.selected) == {c.id for c in candidates}
    assert all(s.status is StepStatus.COMPLETED for s in steps)
    assert [s.ordinal for s in steps] == list(range(1, len(steps) + 1))
    lease = await db.execute(
        text("SELECT lease_owner FROM architecture_workflows WHERE id = :id"), {"id": flow.id}
    )
    assert lease.scalar() is None  # no lease once it stopped running
    listed = await repository.list(project)
    assert [(w.id, w.candidates, w.objective) for w in listed] == [(flow.id, 2, flow.goal.objective)]
    again = await worker(store(connection, "worker-2", clock), Script(), clock).run_once()
    assert again is None  # nothing left to claim


async def test_a_lease_keeps_others_out_until_it_expires_then_another_resumes(
    client: AsyncClient, db: AsyncSession, connection: AsyncConnection, world: World
) -> None:
    flow = await queued(client, db, world)
    clock = Clock()
    first, second = store(connection, "worker-1", clock), store(connection, "worker-2", clock)
    script = Script()
    stopped = await worker(first, script, clock, turns=3).run_once()  # three steps, then the turn runs out
    assert stopped == flow.id
    snapshot = await first.snapshot(flow.id)
    assert snapshot is not None
    assert snapshot.workflow.status is WorkflowStatus.QUEUED  # released: the turn ran out
    assert await first.claim() == flow.id  # worker-1 takes it again, under a fresh lease
    clock.now += timedelta(seconds=30)
    assert await second.claim() is None  # the lease still holds
    clock.now += timedelta(seconds=60)
    assert await second.claim() == flow.id  # the lease expired: worker-2 resumes it
    expected = await first.snapshot(flow.id)
    assert expected is not None
    refused = await first.commit(expected.workflow, expected.workflow.noting("late"), (), None)
    assert refused is False  # worker-1 no longer holds it: nothing written
    resumed = WorkflowController(second, script.executors(), Permissions(), clock=clock)
    finished = await resumed.advance(flow.id)
    assert finished is not None
    assert finished.status is WorkflowStatus.REVIEW_READY
    steps = await SqlAlchemyWorkflowRepository(db).steps(uuid.UUID(world.project_id), flow.id)
    assert len({s.key for s in steps}) == len(steps)  # nothing done twice across workers


async def test_a_persons_cancellation_is_never_overwritten(
    client: AsyncClient, db: AsyncSession, connection: AsyncConnection, world: World
) -> None:
    flow = await queued(client, db, world)
    clock = Clock()
    queue = store(connection, "worker-1", clock)
    assert await queue.claim() == flow.id
    snapshot = await queue.snapshot(flow.id)
    assert snapshot is not None
    async with db.begin_nested():  # a person cancels while the worker works
        await SqlAlchemyWorkflowRepository(db).save(snapshot.workflow.cancel(USER, AT))
    assert await queue.commit(snapshot.workflow, snapshot.workflow.noting("x"), (), None) is False
    stored = await SqlAlchemyWorkflowRepository(db).get(uuid.UUID(world.project_id), flow.id)
    assert stored is not None
    assert stored.status is WorkflowStatus.CANCELLED


async def test_the_records_are_guarded(
    client: AsyncClient, db: AsyncSession, connection: AsyncConnection, world: World
) -> None:
    flow = await queued(client, db, world)
    clock = Clock()
    await worker(store(connection, "worker-1", clock), Script(), clock).run_once()
    for statement, message in (
        ("UPDATE architecture_workflows SET goal = '{}'::jsonb", "request of a workflow does not change"),
        ("DELETE FROM architecture_workflows", "a workflow is kept"),
        ("UPDATE workflow_candidates SET content_hash = repeat('0', 64)", "keeps its architecture"),
        ("UPDATE workflow_candidates SET parent_id = NULL WHERE ordinal = 2", "keeps its architecture"),
        ("DELETE FROM workflow_candidates", "a candidate is kept"),
        ("UPDATE workflow_steps SET note = 'rewritten'", "append-only"),
        ("DELETE FROM workflow_steps", "append-only"),
        ("TRUNCATE workflow_steps CASCADE", "append-only"),
    ):
        with pytest.raises(DBAPIError, match=message):
            async with db.begin_nested():
                await db.execute(text(statement))
    async with db.begin_nested():  # a person rejects it: finished
        repository = SqlAlchemyWorkflowRepository(db)
        current = await repository.get(uuid.UUID(world.project_id), flow.id)
        assert current is not None
        await repository.save(current.reject("Not now.", USER, AT))
    with pytest.raises(DBAPIError, match="a finished workflow does not change"):
        async with db.begin_nested():
            await db.execute(text("UPDATE architecture_workflows SET limitations = '[]'::jsonb"))
    with pytest.raises(IntegrityError):  # an operation's attempt is recorded once
        async with db.begin_nested():
            await db.execute(text(DUPLICATE_ATTEMPT))
