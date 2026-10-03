"""Stored workflows, candidates and steps read back exactly as they were (Autonomous Architecture
Workflow, phase 4), and a record that no longer holds is refused, never half-read."""

import uuid
from datetime import UTC, datetime

import pytest

from core.architecture_ir.serialization import to_dict
from core.domain.architecture_agent.proposals import Answer, ClarificationQuestion
from core.domain.architecture_agent.requests import BaseRevision
from core.domain.architecture_agent.values import QuestionKind
from core.domain.architecture_workflow.candidates import FindingRef
from core.domain.architecture_workflow.errors import InvalidWorkflowRecord
from core.domain.architecture_workflow.goals import WorkflowGoal
from core.domain.architecture_workflow.records import (
    candidate_document,
    candidate_from,
    step_document,
    step_from,
    workflow_document,
    workflow_from,
)
from core.domain.architecture_workflow.values import CandidateOrigin, FailureCode, InputKind
from core.domain.architecture_workflow.workflows import InputRequest
from core.domain.simulations.scenarios import Scenario
from tests.unit.architecture_workflow.test_workflow_controller import ir
from tests.unit.architecture_workflow.test_workflow_domain import (
    USER,
    candidate,
    report,
    running,
    step,
    workflow,
)

AT = datetime(2026, 10, 3, tzinfo=UTC)
OUTAGE = Scenario.from_dict({"name": "Outage", "failures": [{"kind": "component", "target": "api"}]})
QUESTION = ClarificationQuestion(QuestionKind.MISSING_CONCERN, "What availability is required?")


def test_a_workflow_reads_back_as_it_was() -> None:
    goal = WorkflowGoal(
        "Rides", ("AWS",), ("managed",), ("mobile",), "Launch soon.", BaseRevision(uuid.uuid4(), 3),
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), OUTAGE,
    )  # fmt: skip
    waiting = (
        workflow(goal=goal).start(AT).ask(InputRequest(InputKind.CLARIFICATION, questions=(QUESTION,)), AT)
    )
    answered = waiting.provide_input(USER, AT, answers=(Answer(QUESTION.id, "99.9%", USER, AT),))
    for flow in (waiting, answered, running().fail(FailureCode.ENGINE_ERROR, "An engine failed.", AT)):
        assert workflow_from(workflow_document(flow)) == flow


def test_a_workflow_that_does_not_hold_is_refused() -> None:
    document = workflow_document(running().fail(FailureCode.ENGINE_ERROR, "An engine failed.", AT))
    with pytest.raises(InvalidWorkflowRecord):
        workflow_from(document | {"failure": None})  # failed, without why
    with pytest.raises(InvalidWorkflowRecord):
        workflow_from(document | {"status": "winner"})


def test_a_candidate_reads_back_as_it_was_and_never_as_another_architecture() -> None:
    first = candidate().with_report(report(0)).validated()
    improved = candidate(
        workflow_id=first.workflow_id, ordinal=2, origin=CandidateOrigin.RULE, parent_id=first.id,
        trigger=FindingRef("reliability", "rel_x", "high", ("db",)), rule="add-replica", reason="Answers it.",
        ir=ir("api", "db", "db-2"),
    )  # fmt: skip
    project = uuid.uuid4()
    for c in (first, improved):
        assert candidate_from(candidate_document(project, c)) == c
    tampered = candidate_document(project, first) | {"ir": to_dict(ir("api"))}
    with pytest.raises(InvalidWorkflowRecord):
        candidate_from(tampered)  # its content no longer matches the hash it was reviewed by


def test_a_step_reads_back_as_it_was() -> None:
    done = step()
    assert step_from(step_document(uuid.uuid4(), done)) == done
    with pytest.raises(InvalidWorkflowRecord):
        step_from(step_document(uuid.uuid4(), done) | {"key": "wst_other"})
