"""A workflow, its candidates and its steps as stored: JSON documents for their parts, read back into
the same domain objects.

Reading is strict — every part is rebuilt through its own constructor, so a stored record that no
longer satisfies the domain's rules is refused (``InvalidWorkflowRecord``), never half-read. A
candidate's architecture is stored as the IR's own JSON and read by the IR's own reader; its content
hash must be the one stored. Never stored: prompts, context, retrieved text or model output.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.serialization import from_dict, to_dict
from core.domain.architecture_agent.records import answer_from, question_from, report_from
from core.domain.architecture_agent.requests import BaseRevision
from core.domain.architecture_agent.results import EvidenceRef
from core.domain.errors import DomainError
from core.domain.simulations.scenarios import Scenario

from .budget import WorkflowBudget, WorkflowUsage
from .candidates import FindingRef, WorkflowCandidate
from .errors import InvalidWorkflowRecord
from .goals import WorkflowGoal
from .steps import WorkflowStep
from .values import (
    Action,
    CandidateOrigin,
    CandidateStatus,
    FailureCode,
    InputKind,
    Stage,
    StepStatus,
    WorkflowStatus,
)
from .workflows import ApprovedRevision, ArchitectureWorkflow, InputRequest, StatusEvent, WorkflowFailure

UNREADABLE = (KeyError, TypeError, ValueError, AttributeError, DomainError, InvalidArchitecture)


def _id(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _required_id(value: object) -> uuid.UUID:
    found = _id(value)
    if found is None:
        raise ValueError("id")
    return found


def _refused(error: Exception) -> InvalidWorkflowRecord:
    return InvalidWorkflowRecord(details={"fields": [type(error).__name__]})


# --- the workflow -------------------------------------------------------------------------------------


def _input_document(request: InputRequest | None) -> dict[str, Any] | None:
    if request is None:
        return None
    return {
        "kind": request.kind.value,
        "analysis_id": str(request.analysis_id) if request.analysis_id else None,
        "questions": [q.to_dict() for q in request.questions],
    }


def workflow_document(workflow: ArchitectureWorkflow) -> dict[str, Any]:
    """Every stored part of the workflow, by column."""
    approved = workflow.approved
    return {
        "id": workflow.id,
        "project_id": workflow.project_id,
        "requested_by_user_id": workflow.requested_by_user_id,
        "requested_at": workflow.requested_at,
        "goal": workflow.goal.to_dict(),
        "budget": workflow.budget.to_dict(),
        "status": workflow.status.value,
        "stage": workflow.stage.value,
        "iteration": workflow.iteration,
        "usage": workflow.usage.to_dict(),
        "requirement_analysis_id": workflow.requirement_analysis_id,
        "requirement_set_id": workflow.requirement_set_id,
        "input_request": _input_document(workflow.input_request),
        "answers": [a.to_dict() for a in workflow.answers],
        "selected": [str(c) for c in workflow.selected],
        "approved_candidate_id": approved.candidate_id if approved else None,
        "approved_architecture_id": approved.architecture_id if approved else None,
        "approved_revision_number": approved.number if approved else None,
        "decision_reason": workflow.decision_reason,
        "failure": workflow.failure.to_dict() if workflow.failure else None,
        "history": [e.to_dict() for e in workflow.history],
        "limitations": list(workflow.limitations),
        "started_at": workflow.started_at,
        "completed_at": workflow.completed_at,
    }


def goal_from(d: Mapping[str, Any]) -> WorkflowGoal:
    base = d["base"]
    return WorkflowGoal(
        d["objective"],
        tuple(d["constraints"]),
        tuple(d["preferences"]),
        tuple(d["exclusions"]),
        d["context"],
        BaseRevision(_required_id(base["architecture_id"]), base["number"]) if base else None,
        _id(d["requirement_set_id"]),
        _id(d["capacity_analysis_id"]),
        _id(d["cost_analysis_id"]),
        Scenario.from_dict(d["scenario"]) if d["scenario"] is not None else None,
    )


def _input_from(d: Mapping[str, Any] | None) -> InputRequest | None:
    if d is None:
        return None
    questions = tuple(question_from(q) for q in d["questions"])
    return InputRequest(InputKind(d["kind"]), _id(d["analysis_id"]), questions)


def _event(d: Mapping[str, Any]) -> StatusEvent:
    return StatusEvent(
        WorkflowStatus(d["status"]), Stage(d["stage"]), datetime.fromisoformat(d["at"]), _id(d["user_id"])
    )


def _failure(d: Mapping[str, Any] | None) -> WorkflowFailure | None:
    return WorkflowFailure(FailureCode(d["code"]), d["message"], Stage(d["stage"])) if d else None


def workflow_from(row: Mapping[str, Any]) -> ArchitectureWorkflow:
    """The workflow a stored row describes (``InvalidWorkflowRecord`` for one that does not hold)."""
    try:
        candidate = _id(row["approved_candidate_id"])
        approved = (
            ApprovedRevision(
                candidate, _required_id(row["approved_architecture_id"]), row["approved_revision_number"]
            )
            if candidate is not None
            else None
        )
        return ArchitectureWorkflow(
            id=row["id"],
            project_id=row["project_id"],
            requested_by_user_id=row["requested_by_user_id"],
            requested_at=row["requested_at"],
            goal=goal_from(row["goal"]),
            budget=WorkflowBudget(**row["budget"]),
            status=WorkflowStatus(row["status"]),
            stage=Stage(row["stage"]),
            iteration=row["iteration"],
            usage=WorkflowUsage(**row["usage"]),
            requirement_analysis_id=_id(row["requirement_analysis_id"]),
            requirement_set_id=_id(row["requirement_set_id"]),
            input_request=_input_from(row["input_request"]),
            answers=tuple(answer_from(a) for a in row["answers"]),
            selected=tuple(_required_id(c) for c in row["selected"]),
            approved=approved,
            decision_reason=row["decision_reason"],
            failure=_failure(row["failure"]),
            history=tuple(_event(e) for e in row["history"]),
            limitations=tuple(row["limitations"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )
    except InvalidWorkflowRecord:
        raise
    except UNREADABLE as error:
        raise _refused(error) from error


# --- candidates ---------------------------------------------------------------------------------------


def candidate_document(project_id: uuid.UUID, candidate: WorkflowCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "workflow_id": candidate.workflow_id,
        "project_id": project_id,
        "ordinal": candidate.ordinal,
        "parent_id": candidate.parent_id,
        "origin": candidate.origin.value,
        "reason": candidate.reason,
        "ir": to_dict(candidate.ir),
        "content_hash": candidate.content_hash,
        "status": candidate.status.value,
        "trigger": candidate.trigger.to_dict() if candidate.trigger else None,
        "rule": candidate.rule,
        "agent_run_id": candidate.agent_run_id,
        "reports": [r.to_dict() for r in candidate.reports],
        "assumptions": list(candidate.assumptions),
        "rationale": list(candidate.rationale),
        "evidence": [e.to_dict() for e in candidate.evidence],
        "limitations": list(candidate.limitations),
        "generated_at": candidate.created_at,
    }


def candidate_from(row: Mapping[str, Any]) -> WorkflowCandidate:
    """The candidate a stored row describes; its architecture must have the stored content hash."""
    try:
        trigger = row["trigger"]
        return WorkflowCandidate(
            id=row["id"],
            workflow_id=row["workflow_id"],
            ordinal=row["ordinal"],
            origin=CandidateOrigin(row["origin"]),
            reason=row["reason"],
            ir=from_dict(row["ir"]),
            content_hash=row["content_hash"],
            created_at=row["generated_at"],
            parent_id=_id(row["parent_id"]),
            trigger=FindingRef(
                trigger["engine"], trigger["finding_id"], trigger["severity"], tuple(trigger["elements"])
            )
            if trigger
            else None,
            rule=row["rule"],
            agent_run_id=_id(row["agent_run_id"]),
            status=CandidateStatus(row["status"]),
            reports=tuple(report_from(r) for r in row["reports"]),
            assumptions=tuple(row["assumptions"]),
            rationale=tuple(row["rationale"]),
            evidence=tuple(
                EvidenceRef(e["chunk_id"], e["source_id"], e["source_version"], e["reference"])
                for e in row["evidence"]
            ),
            limitations=tuple(row["limitations"]),
        )
    except InvalidWorkflowRecord:
        raise
    except UNREADABLE as error:
        raise _refused(error) from error


# --- steps --------------------------------------------------------------------------------------------


def step_document(project_id: uuid.UUID, step: WorkflowStep) -> dict[str, Any]:
    return {
        "workflow_id": step.workflow_id,
        "project_id": project_id,
        "key": step.key,
        "attempt": step.attempt,
        "ordinal": step.ordinal,
        "iteration": step.iteration,
        "action": step.action.value,
        "stage": step.stage.value,
        "status": step.status.value,
        "started_at": step.started_at,
        "completed_at": step.completed_at,
        "subject": step.subject,
        "candidate_id": step.candidate_id,
        "outputs": dict(step.outputs),
        "usage": step.usage.to_dict(),
        "error": step.error,
        "retryable": step.retryable,
        "note": step.note,
    }


def step_from(row: Mapping[str, Any]) -> WorkflowStep:
    try:
        return WorkflowStep(
            key=row["key"],
            workflow_id=row["workflow_id"],
            ordinal=row["ordinal"],
            iteration=row["iteration"],
            action=Action(row["action"]),
            stage=Stage(row["stage"]),
            status=StepStatus(row["status"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            subject=row["subject"],
            attempt=row["attempt"],
            candidate_id=_id(row["candidate_id"]),
            outputs=dict(row["outputs"]),
            usage=WorkflowUsage(**row["usage"]),
            error=row["error"],
            retryable=row["retryable"],
            note=row["note"],
        )
    except InvalidWorkflowRecord:
        raise
    except UNREADABLE as error:
        raise _refused(error) from error
