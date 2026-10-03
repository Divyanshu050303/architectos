"""Recovery (Autonomous Architecture Workflow, phase 8): a workflow stopped after any step — after the
requirement analysis, retrieval, generation, validation, an analysis or a comparison — resumes from its
last completed step and repeats nothing; a commit lost to a database failure loses only that step,
which runs again; an engine that cannot run is reported as failed, never as a result."""

import uuid
from dataclasses import dataclass, replace
from typing import Any, cast

import pytest

from core.domain.architecture_diff.ports import CapacityInputs
from core.domain.architecture_workflow.budget import WorkflowBudget
from core.domain.architecture_workflow.candidates import WorkflowCandidate
from core.domain.architecture_workflow.controller import WorkflowController
from core.domain.architecture_workflow.planner import Inputs
from core.domain.architecture_workflow.steps import WorkflowStep
from core.domain.architecture_workflow.values import Action, StepStatus, WorkflowStatus
from core.domain.architecture_workflow.workflows import ArchitectureWorkflow
from engines.architecture_workflow.executors import build_executors
from tests.unit.architecture_workflow.test_workflow_controller import (
    START,
    Clock,
    Permissions,
    Script,
    Store,
    workflow,
)
from tests.unit.architecture_workflow.test_workflow_executors import (
    AT,
    WORKFLOW_ENGINES,
    Analyzer,
    Knowledge,
    Loader,
    pipeline,
)
from tests.unit.simulation.test_simulation_engine import workload

A = Action
WITH_INPUTS = Inputs(workload=True, scenario=True)  # capacity and simulation run too


def _store() -> Store:
    return Store(workflow(), inputs=WITH_INPUTS)


async def _finish(store: Store, script: Script) -> ArchitectureWorkflow:
    controller = WorkflowController(store, script.executors(), Permissions(), clock=Clock())
    if store.workflow.status is WorkflowStatus.RUNNING:
        finished = await controller.advance(store.workflow.id)
        assert finished is not None
    return store.workflow


async def _reference() -> tuple[list[Action], list[Action]]:
    store, script = _store(), Script()
    await _finish(store, script)
    assert store.workflow.status is WorkflowStatus.REVIEW_READY
    return [s.action for s in store.steps], list(script.executed)


async def test_the_reference_run_passes_every_named_stage() -> None:
    steps, _ = await _reference()
    for action in (
        A.RETRIEVE_KNOWLEDGE, A.GENERATE_ARCHITECTURE, A.VALIDATE_ARCHITECTURE, A.RUN_CAPACITY_ANALYSIS,
        A.RUN_SIMULATION, A.GENERATE_ALTERNATIVE, A.COMPARE_CANDIDATES, A.PREPARE_REVIEW,
    ):  # fmt: skip
        assert action in steps, action


@pytest.mark.parametrize("stop", range(1, 18))  # the reference run takes 18 steps
async def test_resuming_after_any_step_repeats_nothing(stop: int) -> None:
    steps, executed = await _reference()
    if stop >= len(steps):
        pytest.skip("the workflow finishes in fewer steps")
    store, script = _store(), Script()
    controller = WorkflowController(store, script.executors(), Permissions(), clock=Clock())
    await controller.advance(store.workflow.id, max_turns=stop)  # the worker stops here
    assert [s.action for s in store.steps] == steps[:stop]
    store.workflow = store.workflow.release(START).start(START)  # another worker resumes it
    finished = await _finish(store, script)
    assert finished.status is WorkflowStatus.REVIEW_READY
    assert [s.action for s in store.steps] == steps
    assert script.executed == executed  # nothing executed twice
    assert len({s.key for s in store.steps}) == len(store.steps)


async def test_resuming_after_the_requirement_analysis_does_not_analyze_again() -> None:
    store, script = Store(workflow(set_id=None, requirement_set_id=None)), Script()
    controller = WorkflowController(store, script.executors(), Permissions(), clock=Clock())
    await controller.advance(store.workflow.id, max_turns=1)
    assert [s.action for s in store.steps] == [A.ANALYZE_REQUIREMENTS]
    store.workflow = store.workflow.release(START).start(START)
    paused = await _finish(store, script)
    assert paused.status is WorkflowStatus.NEEDS_INPUT
    assert script.executed.count(A.ANALYZE_REQUIREMENTS) == 1


@dataclass
class FailingStore(Store):
    """The database goes away during one commit: nothing of it is written."""

    fail_at: int = 0

    async def commit(
        self,
        expected: ArchitectureWorkflow,
        workflow: ArchitectureWorkflow,
        candidates: tuple[WorkflowCandidate, ...],
        step: WorkflowStep | None,
    ) -> bool:
        if step is not None and len(self.steps) + 1 == self.fail_at:
            self.fail_at = 0
            raise ConnectionError("database went away")
        return await super().commit(expected, workflow, candidates, step)


@pytest.mark.parametrize("lost", [2, 3, 5, 9])
async def test_a_lost_commit_loses_only_that_step_which_runs_again(lost: int) -> None:
    steps, executed = await _reference()
    store, script = FailingStore(workflow(), inputs=WITH_INPUTS, fail_at=lost), Script()
    controller = WorkflowController(store, script.executors(), Permissions(), clock=Clock())
    with pytest.raises(ConnectionError):  # the worker stops; its lease will expire
        await controller.advance(store.workflow.id)
    assert len(store.steps) == lost - 1  # everything before it is kept
    store.workflow = store.workflow.release(START).start(START)
    finished = await _finish(store, script)
    assert finished.status is WorkflowStatus.REVIEW_READY
    assert [s.action for s in store.steps] == steps  # recorded once each
    again = list(script.executed)
    del again[lost - 1]  # the lost step ran twice: its first effects were never written
    assert again == executed


class Unavailable:
    def analyze(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("capacity engine down")


async def test_an_engine_that_cannot_run_is_reported_as_failed_never_as_a_result() -> None:
    store = Store(workflow(budget=WorkflowBudget(max_iterations=0)), inputs=Inputs(workload=True))
    executors = build_executors(
        engines=replace(WORKFLOW_ENGINES, capacity=cast(Any, Unavailable())), pipeline=pipeline(),
        loader=Loader(capacity=CapacityInputs(uuid.uuid4(), workload())), analyzer=Analyzer(),
        knowledge=Knowledge(),
    )  # fmt: skip
    controller = WorkflowController(store, executors, Permissions(), clock=lambda: AT)
    reviewed = await controller.advance(store.workflow.id)
    assert reviewed is not None
    assert reviewed.status is WorkflowStatus.REVIEW_READY  # the other results are still reviewable
    [candidate] = store.candidates.values()
    capacity = candidate.report("capacity")
    assert capacity is not None
    assert capacity.status.value == "failed"
    assert capacity.findings == ()  # nothing invented
    step = next(s for s in store.steps if s.action is A.RUN_CAPACITY_ANALYSIS)
    assert (step.status, step.error) == (StepStatus.FAILED, "engine_error")
    assert any("capacity engine could not analyze" in note for note in reviewed.limitations)
