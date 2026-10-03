"""The workflow controller: the server-owned loop that carries a running workflow forward, one
registry action at a time, until it waits for a person, reaches review, stops or is taken away.

Each turn:

1. **Read** the workflow, its candidates and its steps (a snapshot). Anything but ``running`` ends the
   turn — a person cancelled it, or it waits for input.
2. **Plan** the one next decision (deterministic policy; a model never chooses).
3. **Authorize** it: the action must be in the registry, automatic, allowed in this stage — and the
   person who started the workflow must still hold its permission.
4. **Execute** it through its executor. A failure is an outcome, never an exception: an unexpected
   error becomes an ``infrastructure_error`` step.
5. **Record** the step and its effects (usage, candidates, a question for a person, the review
   package) in one commit, accepted only if the workflow is still as it was read. A refused commit
   (cancelled meanwhile, or another worker holds it) ends the turn with nothing written.

A completed step is never executed again under its key; a retryable failure is attempted once more.
"""

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime

from .budget import WorkflowUsage
from .candidates import WorkflowCandidate
from .errors import InvalidWorkflowRecord
from .planner import Next, PlanningState, Stop, plan
from .ports import PermissionCheck, StepContext, StepExecutor, StepOutcome, WorkflowSnapshot, WorkflowStore
from .steps import MAX_ATTEMPTS, WorkflowStep
from .tools import authorize
from .values import Action, FailureCode, StepStatus, WorkflowStatus
from .workflows import ArchitectureWorkflow

log = logging.getLogger("architectos.workflow")

MAX_TURNS = 200  # a hard stop for one call, far above any budget
INFRASTRUCTURE_ERROR = FailureCode.INFRASTRUCTURE_ERROR.value


def _elapsed(workflow: ArchitectureWorkflow, now: datetime) -> float:
    started = workflow.started_at or now
    return max(0.0, (now - started).total_seconds())


def _attempt(steps: tuple[WorkflowStep, ...], key: str) -> int:
    return 1 + sum(1 for s in steps if s.key == key and s.status is StepStatus.FAILED)


def _kept(snapshot: WorkflowSnapshot, outcome: StepOutcome) -> tuple[WorkflowCandidate, ...]:
    """An executor may add candidates or add reports and status to existing ones — never change an
    existing candidate's architecture or lineage."""
    existing = {c.id: c for c in snapshot.candidates}
    for candidate in outcome.candidates:
        before = existing.get(candidate.id)
        if before is not None and (
            before.content_hash != candidate.content_hash
            or before.parent_id != candidate.parent_id
            or before.ordinal != candidate.ordinal
        ):
            raise InvalidWorkflowRecord(details={"fields": ["candidate.content_hash"]})
    return outcome.candidates


class WorkflowController:
    def __init__(
        self,
        store: WorkflowStore,
        executors: Mapping[Action, StepExecutor],
        permissions: PermissionCheck,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._executors = executors
        self._permissions = permissions
        self._clock = clock

    async def advance(
        self, workflow_id: uuid.UUID, *, max_turns: int = MAX_TURNS
    ) -> ArchitectureWorkflow | None:
        """Carries the workflow forward until it stops running (or this call's turns run out)."""
        latest: ArchitectureWorkflow | None = None
        for _ in range(max_turns):
            snapshot = await self._store.snapshot(workflow_id)
            if snapshot is None:
                return None
            latest = snapshot.workflow
            if latest.status is not WorkflowStatus.RUNNING:
                return latest
            moved = await self._turn(snapshot)
            if moved is None:  # the commit was refused: someone else changed it
                current = await self._store.snapshot(workflow_id)
                return current.workflow if current else None
            latest = moved
            if moved.status is not WorkflowStatus.RUNNING:
                return moved
        return latest

    async def _stop(
        self, snapshot: WorkflowSnapshot, code: FailureCode, message: str
    ) -> ArchitectureWorkflow | None:
        failed = snapshot.workflow.fail(code, message, self._clock())
        committed = await self._store.commit(snapshot.workflow, failed, (), None)
        return failed if committed else None

    async def _turn(self, snapshot: WorkflowSnapshot) -> ArchitectureWorkflow | None:
        flow, now = snapshot.workflow, self._clock()
        state = PlanningState(flow, snapshot.candidates, snapshot.steps, snapshot.inputs, _elapsed(flow, now))
        decision = plan(state)
        if isinstance(decision, Stop):
            return await self._stop(snapshot, decision.code, decision.message)
        staged = flow.at_stage(decision.stage)
        spec = authorize(decision.action, staged.status, staged.stage)  # a refusal is a planner bug: raised
        if not await self._permissions.allowed(staged, spec.permission):
            message = f"The person who started the workflow no longer holds {spec.permission.value}."
            return await self._stop(snapshot, FailureCode.PERMISSION_DENIED, message)
        attempt = _attempt(snapshot.steps, decision.key)
        context = StepContext(
            staged, snapshot.candidates, decision, attempt, now, len(snapshot.candidates) + 1
        )
        outcome = await self._execute(context)
        failed = outcome.status is StepStatus.FAILED
        # An outage (infrastructure_error) is retried once for any action; a failure the action itself
        # reports, only when the registry says the action may be retried.
        outage = outcome.error == INFRASTRUCTURE_ERROR
        retryable = failed and outcome.retryable and (spec.retryable or outage) and attempt < MAX_ATTEMPTS
        step = WorkflowStep(
            decision.key, flow.id, len(snapshot.steps) + 1, decision.iteration, decision.action,
            decision.stage, outcome.status, now, max(now, self._clock()), decision.subject, attempt,
            decision.candidate_id, dict(outcome.outputs), outcome.usage, outcome.error, retryable,
            outcome.note,
        )  # fmt: skip
        moved = self._apply(staged, decision, outcome, step)
        candidates = _kept(snapshot, outcome)
        committed = await self._store.commit(flow, moved, candidates, step)
        return moved if committed else None

    async def _execute(self, context: StepContext) -> StepOutcome:
        executor = self._executors.get(context.decision.action)
        if executor is None:
            return StepOutcome(StepStatus.FAILED, error="not_configured", note="No executor is configured.")
        try:
            return await executor.execute(context)
        except Exception as error:  # an executor bug or an outage: recorded, never raised past here
            log.error(
                "workflow step failed",
                extra={"workflow_id": str(context.workflow.id), "error_type": type(error).__name__},
            )
            note = "The step could not be completed."
            return StepOutcome(StepStatus.FAILED, error=INFRASTRUCTURE_ERROR, retryable=True, note=note)

    def _apply(
        self, flow: ArchitectureWorkflow, decision: Next, outcome: StepOutcome, step: WorkflowStep
    ) -> ArchitectureWorkflow:
        now = self._clock()
        alternative = decision.action is Action.GENERATE_ALTERNATIVE
        counted = WorkflowUsage(tool_calls=1, iterations=1 if alternative else 0)
        moved = flow.counted(outcome.usage.plus(counted))
        if alternative:
            moved = moved.next_iteration()
        if outcome.requirement_analysis_id is not None:
            moved = moved.with_requirements(analysis_id=outcome.requirement_analysis_id)
        if outcome.limitations:
            moved = moved.noting(*outcome.limitations)
        if step.status is not StepStatus.COMPLETED:
            return moved
        if outcome.ask is not None:
            return moved.ask(outcome.ask, now)
        if decision.action is Action.PREPARE_REVIEW:
            if decision.reason:
                moved = moved.noting(
                    f"The workflow reached its {decision.reason} limit; this is what it had."
                )
            return moved.ready_for_review(outcome.selected, now)
        return replace(moved)
