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
"""

import asyncio
import logging
import uuid
from typing import Protocol

from core.domain.architecture_workflow.controller import WorkflowController
from core.domain.architecture_workflow.values import WorkflowStatus

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
