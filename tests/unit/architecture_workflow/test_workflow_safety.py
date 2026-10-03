"""The workflow's safety properties that hold in code (Autonomous Architecture Workflow, phase 6):

- nothing the workflow runs on its own can write an architecture or reach outside ArchitectOS — the
  only architecture write is a person's approval, in the service;
- a step whose result does not hold (rewriting a candidate's architecture or lineage) stops the
  workflow with nothing of it kept — never retried forever, never half-applied;
- every step and stop is observable with identifiers, codes, counts and durations — never the goal.
"""

import ast
import logging
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from core.architecture_ir.serialization import content_hash
from core.domain.architecture_workflow.controller import WorkflowController
from core.domain.architecture_workflow.goals import WorkflowGoal
from core.domain.architecture_workflow.ports import StepContext, StepOutcome
from core.domain.architecture_workflow.values import Action, FailureCode, StepStatus, WorkflowStatus
from core.domain.metrics import check_labels
from tests.unit.architecture_workflow.test_workflow_controller import (
    Clock,
    Permissions,
    Script,
    Store,
    ir,
    workflow,
)

ROOT = Path(__file__).resolve().parents[3]
AUTONOMOUS = [
    *sorted((ROOT / "core/domain/architecture_workflow").glob("*.py")),
    *sorted((ROOT / "engines/architecture_workflow").glob("*.py")),
    ROOT / "workers/workflow_worker.py",
]
PERSON_ONLY = {"workflow_service.py"}  # a person's moves, approval among them
REACHING_OUT = {
    "subprocess", "shutil", "ctypes", "pickle", "marshal", "importlib", "httpx", "requests", "urllib",
    "aiohttp", "smtplib", "ftplib", "telnetlib", "webbrowser",
}  # fmt: skip
EVALUATING = {"eval", "exec", "compile", "__import__"}
ARCHITECTURE_READS = {"get", "get_revision"}
CANARY = "Canary objective 77c1"


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for item in ast.walk(tree):
        if isinstance(item, ast.Import):
            found |= {alias.name for alias in item.names}
        elif isinstance(item, ast.ImportFrom) and item.module:
            found.add(item.module)
    return found


@pytest.mark.parametrize("path", AUTONOMOUS, ids=lambda p: p.name)
def test_nothing_the_workflow_runs_can_write_an_architecture_or_reach_outside(path: Path) -> None:
    tree = ast.parse(path.read_text())
    modules = {m.split(".")[0] for m in _imports(tree)}  # top-level packages (ours are core, engines…)
    assert modules.isdisjoint(REACHING_OUT), path.name
    calls = {c.func.id for c in ast.walk(tree) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert calls.isdisjoint(EVALUATING), path.name
    if path.name in PERSON_ONLY:
        return
    assert not any("architecture_service" in m for m in _imports(tree)), path.name
    for item in ast.walk(tree):  # uow.architectures.<x>: reads only
        if (
            isinstance(item, ast.Attribute)
            and isinstance(item.value, ast.Attribute)
            and item.value.attr == "architectures"
        ):
            assert item.attr in ARCHITECTURE_READS, (path.name, item.attr)


@dataclass
class Rewriting:
    """Validation that tries to replace the candidate's architecture under the same id."""

    async def execute(self, context: StepContext) -> StepOutcome:
        target = context.candidate(context.decision.candidate_id)
        assert target is not None
        other = ir("anything", "else")
        forged = replace(target, ir=other, content_hash=content_hash(other))
        return StepOutcome(StepStatus.COMPLETED, candidates=(forged,))


async def test_a_step_that_rewrites_a_candidate_stops_the_workflow_and_keeps_nothing() -> None:
    store = Store(workflow())
    executors = Script().executors() | {Action.VALIDATE_ARCHITECTURE: Rewriting()}
    controller = WorkflowController(store, executors, Permissions(), clock=Clock())
    stopped = await controller.advance(store.workflow.id)
    assert stopped is not None
    assert stopped.status is WorkflowStatus.FAILED
    assert stopped.failure is not None
    assert stopped.failure.code is FailureCode.ENGINE_ERROR
    [kept] = store.candidates.values()
    assert kept.ir == ir("api", "db")  # the agent's architecture, as it was produced
    assert Action.VALIDATE_ARCHITECTURE not in {s.action for s in store.steps}  # not recorded either


@dataclass
class Recorded:
    counted: list[tuple[str, int, dict[str, str]]] = field(default_factory=list)
    observed: list[tuple[str, float, dict[str, str]]] = field(default_factory=list)

    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        check_labels(labels)
        self.counted.append((name, value, labels))

    def observe(self, name: str, value: float, **labels: str) -> None:
        check_labels(labels)
        self.observed.append((name, value, labels))


def _logged(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(r.getMessage() + repr(r.__dict__) for r in caplog.records)


async def test_every_step_is_observable_without_the_goal(caplog: pytest.LogCaptureFixture) -> None:
    goal = WorkflowGoal(CANARY, constraints=(CANARY,), context=CANARY, requirement_set_id=uuid.uuid4())
    store, metrics = Store(workflow(goal=goal)), Recorded()
    controller = WorkflowController(
        store, Script().executors(), Permissions(), clock=Clock(), metrics=metrics
    )
    caplog.set_level(logging.DEBUG, logger="architectos.workflow")
    finished = await controller.advance(store.workflow.id)
    assert finished is not None
    assert finished.status is WorkflowStatus.REVIEW_READY
    records = [r for r in caplog.records if r.getMessage() == "workflow step"]
    assert len(records) == len(store.steps)
    for record, step in zip(records, store.steps, strict=True):
        assert record.__dict__["workflow_id"] == str(store.workflow.id)
        assert record.__dict__["project_id"] == str(store.workflow.project_id)
        assert record.__dict__["action"] == step.action.value
        assert record.__dict__["duration_ms"] >= 0
    steps = [c for c in metrics.counted if c[0] == "workflow.steps"]
    assert len(steps) == len(store.steps)
    assert {c[2]["action"] for c in steps} == {s.action.value for s in store.steps}
    assert len([o for o in metrics.observed if o[0] == "workflow.step_ms"]) == len(store.steps)
    assert CANARY not in _logged(caplog)


async def test_a_stop_is_observable(caplog: pytest.LogCaptureFixture) -> None:
    store, metrics = Store(workflow()), Recorded()
    script = Script(generation_error="llm_unavailable")
    controller = WorkflowController(store, script.executors(), Permissions(), clock=Clock(), metrics=metrics)
    caplog.set_level(logging.INFO, logger="architectos.workflow")
    stopped = await controller.advance(store.workflow.id)
    assert stopped is not None
    assert stopped.failure is not None
    code = stopped.failure.code.value
    [record] = [r for r in caplog.records if r.getMessage() == "workflow stopped"]
    assert record.__dict__["failure"] == code
    assert ("workflow.failed", 1, {"code": code}) in metrics.counted
