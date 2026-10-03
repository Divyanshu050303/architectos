"""The architecture workflow worker: takes workflows from the database's queue and carries each one
forward with the controller, one leased workflow at a time.

    while not stopping:
        claim a queued workflow — or a running one whose lease expired (its worker stopped)
        advance it until it waits for a person, reaches review, stops, or this turn runs out
        if it is still running (the turn ran out): release it to the queue

No other infrastructure: the queue is the ``architecture_workflows`` table, claimed with
``FOR UPDATE SKIP LOCKED`` under a lease. A worker that crashes holds nothing but its lease; when it
expires, another worker resumes the workflow from its last completed step — completed steps are
never executed again.

Run one or more workers with ``python -m workers.workflow_worker``: each uses the same engines, model
pipeline and settings as the API, its own database sessions, and a unique lease owner.
"""

import asyncio
import logging
import os
import signal
import socket
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings
from core.domain.architecture_workflow.access import ProjectWorkflowAccess
from core.domain.architecture_workflow.controller import WorkflowController
from core.domain.architecture_workflow.values import WorkflowStatus
from core.domain.clock import utc_now
from core.domain.knowledge.knowledge_service import KnowledgeService
from core.domain.requirements.analysis_service import RequirementAnalysisService
from engines.architecture_workflow.executors import WorkflowEngines, build_executors
from persistence.unit_of_work import SqlAlchemyUnitOfWork
from persistence.workflow_store import PostgresWorkflowStore

log = logging.getLogger("architectos.workflow.worker")


class Queue(Protocol):
    async def claim(self) -> uuid.UUID | None: ...

    async def release(self, workflow_id: uuid.UUID) -> None: ...


class WorkflowWorker:
    def __init__(
        self, queue: Queue, controller: WorkflowController, *, turns: int = 50, idle_seconds: float = 2.0
    ) -> None:
        self._queue = queue
        self._controller = controller
        self._turns = turns
        self._idle_seconds = idle_seconds

    async def run_once(self) -> uuid.UUID | None:
        """One claimed workflow carried as far as this turn allows; None when the queue is empty."""
        workflow_id = await self._queue.claim()
        if workflow_id is None:
            return None
        log.info("workflow claimed", extra={"workflow_id": str(workflow_id)})
        try:
            reached = await self._controller.advance(workflow_id, max_turns=self._turns)
        except BaseException:
            await self._queue.release(workflow_id)  # stopping or broken: another worker resumes it
            raise
        if reached is not None and reached.status is WorkflowStatus.RUNNING:
            await self._queue.release(workflow_id)  # the turn ran out: back to the queue
        status = reached.status.value if reached else "missing"
        log.info("workflow advanced", extra={"workflow_id": str(workflow_id), "status": status})
        return workflow_id

    async def run(self, stopping: asyncio.Event) -> None:
        """Until ``stopping`` is set; idle while the queue is empty."""
        while not stopping.is_set():
            try:
                worked = await self.run_once()
            except Exception as error:  # a failure of one workflow never stops the worker
                log.error("workflow worker turn failed", extra={"error_type": type(error).__name__})
                worked = None
            if worked is None:
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=self._idle_seconds)
                except TimeoutError:
                    continue


def build_worker(
    engines: Any, settings: Settings, sessions: Callable[[], AsyncSession], owner: str
) -> WorkflowWorker:
    """A worker over the engines the API builds at startup (``app.state``), with a fresh unit of work
    for every read — so each action sees the person's permissions and the project as they are now."""
    from apps.api.metrics import LogMetrics  # noqa: PLC0415 - the API's metrics sink, only when run

    def uows() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions(), closing=True)

    access = ProjectWorkflowAccess(
        uows,
        lambda: KnowledgeService(uows(), engines.knowledge_engine, metrics=LogMetrics()),
        lambda: RequirementAnalysisService(uows(), engines.requirements_engine, metrics=LogMetrics()),
    )
    executors = build_executors(
        engines=WorkflowEngines(
            engines.validation_engine, engines.reliability_engine, engines.security_engine,
            engines.observability_engine, engines.capacity_engine, engines.cost_engine,
            engines.simulation_engine, engines.evolution_engine, engines.diff_engine,
        ),
        pipeline=engines.agent_pipeline, loader=access, analyzer=access, knowledge=access,
    )  # fmt: skip
    queue = PostgresWorkflowStore(
        sessions, owner, lease_seconds=settings.architecture_workflow_lease_seconds, clock=utc_now
    )
    controller = WorkflowController(queue, executors, access, clock=utc_now, metrics=LogMetrics())
    return WorkflowWorker(
        queue,
        controller,
        turns=settings.architecture_workflow_worker_turns,
        idle_seconds=settings.architecture_workflow_worker_idle_seconds,
    )


async def main() -> None:  # pragma: no cover - the process entry point
    from apps.api.config import get_settings  # noqa: PLC0415
    from apps.api.logging_config import configure_logging  # noqa: PLC0415
    from apps.api.main import create_app  # noqa: PLC0415 - the same engines as the API
    from persistence.database import create_engine, create_session_factory  # noqa: PLC0415

    settings = get_settings()
    configure_logging(settings.log_level)
    app = create_app(settings)
    database = create_engine(str(settings.database_url), echo=settings.database_echo)
    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    worker = build_worker(app.state, settings, create_session_factory(database), owner)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(stop, stopping.set)
    log.info("workflow worker started", extra={"owner": owner})
    try:
        await worker.run(stopping)
    finally:
        await database.dispose()
        log.info("workflow worker stopped", extra={"owner": owner})


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
