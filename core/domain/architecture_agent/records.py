"""An agent run as stored: JSON documents for its parts, read back into the same domain objects.

Reading is strict — every part is rebuilt through its own constructor, so a stored run that no
longer satisfies the domain's rules is refused (``InvalidAgentRecord``), never half-read. The
candidate's architecture is stored as the IR's own JSON (``to_dict``) and read by the IR's own reader,
and its content hash must be the one that was reviewed. Never stored: the prompt, the context, the
retrieved text and the model's raw output (only its SHA-256 and size).
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.serialization import from_dict, to_dict

from .errors import InvalidAgentRecord, InvalidAgentRequest
from .proposals import (
    Answer,
    Claim,
    ClarificationQuestion,
    DesignDecision,
    Proposal,
    ProposedConnection,
    ProposedNode,
)
from .requests import AgentRequest, AgentUsage, BaseRevision, Budget
from .results import AgentFinding, Candidate, EngineReport, EvidenceRef, Rejection
from .runs import AcceptedRevision, AgentRun, RawOutput, RunFailure, StatusEvent
from .values import Basis, EngineStatus, FailureCode, QuestionKind, RunStatus, Stage

USAGE_FIELDS = (
    "model_calls", "input_tokens", "output_tokens", "model_latency_ms", "retrieval_calls", "engine_runs",
)  # fmt: skip


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _from_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


# --- writing ------------------------------------------------------------------------------------


def _node(n: ProposedNode) -> dict[str, Any]:
    return {
        "id": n.id,
        "kind": n.kind,
        "name": n.name,
        "rationale": n.rationale,
        "component": n.component,
        "technology": n.technology,
        "configuration": n.configuration,
        "requirement_refs": list(n.requirement_refs),
        "evidence": list(n.evidence),
        "confidence": _decimal(n.confidence),
    }


def _connection(c: ProposedConnection) -> dict[str, Any]:
    return {
        "id": c.id,
        "source": c.source,
        "target": c.target,
        "kind": c.kind,
        "rationale": c.rationale,
        "protocol": c.protocol,
        "requirement_refs": list(c.requirement_refs),
        "evidence": list(c.evidence),
        "confidence": _decimal(c.confidence),
    }


def _decision(d: DesignDecision) -> dict[str, Any]:
    return {
        "title": d.title,
        "choice": d.choice,
        "rationale": d.rationale,
        "alternatives": list(d.alternatives),
        "trade_offs": list(d.trade_offs),
        "requirement_refs": list(d.requirement_refs),
        "evidence": list(d.evidence),
    }


def proposal_document(proposal: Proposal) -> dict[str, Any]:
    return {
        "name": proposal.name,
        "summary": proposal.summary,
        "confidence": _decimal(proposal.confidence),
        "nodes": [_node(n) for n in proposal.nodes],
        "connections": [_connection(c) for c in proposal.connections],
        "decisions": [_decision(d) for d in proposal.decisions],
        "claims": [c.to_dict() for c in proposal.claims],
        "risks": list(proposal.risks),
        "questions": list(proposal.questions),
    }


def candidate_document(candidate: Candidate) -> dict[str, Any]:
    return {
        "ir": to_dict(candidate.ir),
        "content_hash": candidate.content_hash,
        "normalizations": list(candidate.normalizations),
        "evidence": [e.to_dict() for e in candidate.evidence],
        "uncovered_requirements": list(candidate.uncovered_requirements),
    }


def run_document(run: AgentRun) -> dict[str, Any]:
    """Every stored part of the run, by column."""
    accepted, base, candidate, raw = run.accepted, run.request.base, run.candidate, run.raw_output
    return {
        "id": run.id,
        "project_id": run.project_id,
        "requested_by_user_id": run.requested_by_user_id,
        "requested_at": run.requested_at,
        "requirement_set_id": run.request.requirement_set_id,
        "base_architecture_id": base.architecture_id if base else None,
        "base_revision_number": base.number if base else None,
        "request": run.request.to_dict(),
        "budget": run.budget.to_dict(),
        "status": run.status.value,
        "stage": run.stage.value,
        "usage": {k: getattr(run.usage, k) for k in USAGE_FIELDS},
        "model": run.model,
        "prompt_version": run.prompt_version,
        "questions": [q.to_dict() for q in run.questions],
        "answers": [a.to_dict() for a in run.answers],
        "proposal": proposal_document(run.proposal) if run.proposal else None,
        "rejections": [r.to_dict() for r in run.rejections],
        "candidate": candidate_document(candidate) if candidate else None,
        "candidate_content_hash": candidate.content_hash if candidate else None,
        "reports": [r.to_dict() for r in run.reports],
        "raw_output_sha256": raw.sha256 if raw else None,
        "raw_output_bytes": raw.bytes if raw else None,
        "failure": run.failure.to_dict() if run.failure else None,
        "accepted_architecture_id": accepted.architecture_id if accepted else None,
        "accepted_revision_number": accepted.number if accepted else None,
        "decision_reason": run.decision_reason,
        "history": [e.to_dict() for e in run.history],
        "limitations": list(run.limitations),
        "completed_at": run.completed_at,
    }


# --- reading ------------------------------------------------------------------------------------


def _request(d: Mapping[str, Any]) -> AgentRequest:
    base = d.get("base")
    return AgentRequest(
        uuid.UUID(d["requirement_set_id"]),
        d["objective"],
        tuple(d["constraints"]),
        tuple(d["preferences"]),
        tuple(d["exclusions"]),
        d.get("context"),
        BaseRevision(uuid.UUID(base["architecture_id"]), base["number"]) if base else None,
    )


def question_from(d: Mapping[str, Any]) -> ClarificationQuestion:
    question = ClarificationQuestion(
        QuestionKind(d["kind"]), d["question"], tuple(d["requirement_refs"]), d["blocking"]
    )
    if question.id != d["id"]:
        raise InvalidAgentRecord(details={"fields": ["question.id"]})
    return question


def answer_from(d: Mapping[str, Any]) -> Answer:
    by, at = uuid.UUID(d["answered_by_user_id"]), datetime.fromisoformat(d["answered_at"])
    return Answer(d["question_id"], d["answer"], by, at)


def proposal_from(d: Mapping[str, Any]) -> Proposal:
    nodes = tuple(
        ProposedNode(
            n["id"], n["kind"], n["name"], n["rationale"], n.get("component"), n.get("technology"),
            n.get("configuration"), tuple(n["requirement_refs"]), tuple(n["evidence"]),
            _from_decimal(n.get("confidence")),
        )
        for n in d["nodes"]
    )  # fmt: skip
    connections = tuple(
        ProposedConnection(
            c["id"], c["source"], c["target"], c["kind"], c["rationale"], c.get("protocol"),
            tuple(c["requirement_refs"]), tuple(c["evidence"]), _from_decimal(c.get("confidence")),
        )
        for c in d["connections"]
    )  # fmt: skip
    decisions = tuple(
        DesignDecision(
            x["title"], x["choice"], x["rationale"], tuple(x["alternatives"]), tuple(x["trade_offs"]),
            tuple(x["requirement_refs"]), tuple(x["evidence"]),
        )
        for x in d["decisions"]
    )  # fmt: skip
    claims = tuple(
        Claim(
            c["statement"], Basis(c["basis"]), tuple(c["requirement_refs"]), tuple(c["evidence"]),
            _from_decimal(c.get("confidence")),
        )
        for c in d["claims"]
    )  # fmt: skip
    return Proposal(
        d["name"], d["summary"], nodes, connections, decisions, claims,
        tuple(d["risks"]), tuple(d["questions"]), _from_decimal(d.get("confidence")),
    )  # fmt: skip


def candidate_from(d: Mapping[str, Any]) -> Candidate:
    evidence = tuple(
        EvidenceRef(e["chunk_id"], e["source_id"], e["source_version"], e["reference"]) for e in d["evidence"]
    )
    candidate = Candidate(
        from_dict(d["ir"]), tuple(d["normalizations"]), evidence, tuple(d["uncovered_requirements"])
    )
    if candidate.content_hash != d["content_hash"]:  # never another architecture than was reviewed
        raise InvalidAgentRecord(details={"fields": ["candidate.content_hash"]})
    return candidate


def report_from(d: Mapping[str, Any]) -> EngineReport:
    findings = tuple(
        AgentFinding(f["engine"], f["rule"], f["severity"], f["message"], tuple(f["elements"]))
        for f in d["findings"]
    )
    status = EngineStatus(d["status"])
    return EngineReport(
        d["engine"],
        status,
        dict(d["versions"]),
        findings,
        dict(d["summary"]),
        tuple(d["limitations"]),
        d.get("error"),
    )


def _event(d: Mapping[str, Any]) -> StatusEvent:
    user = d.get("user_id")
    at = datetime.fromisoformat(d["at"])
    return StatusEvent(RunStatus(d["status"]), Stage(d["stage"]), at, uuid.UUID(user) if user else None)


def _failure(d: Mapping[str, Any] | None) -> RunFailure | None:
    return None if d is None else RunFailure(FailureCode(d["code"]), d["message"], Stage(d["stage"]))


def run_from(row: Mapping[str, Any]) -> AgentRun:
    """The run a stored row describes (``InvalidAgentRecord`` for one that does not hold)."""
    try:
        candidate = candidate_from(row["candidate"]) if row["candidate"] else None
        accepted_id = row["accepted_architecture_id"]
        accepted = (
            AcceptedRevision(accepted_id, row["accepted_revision_number"], candidate.content_hash)
            if accepted_id is not None and candidate is not None
            else None
        )
        sha256 = row["raw_output_sha256"]
        return AgentRun(
            id=row["id"],
            project_id=row["project_id"],
            requested_by_user_id=row["requested_by_user_id"],
            requested_at=row["requested_at"],
            request=_request(row["request"]),
            budget=Budget(**row["budget"]),
            status=RunStatus(row["status"]),
            stage=Stage(row["stage"]),
            usage=AgentUsage(**{k: row["usage"][k] for k in USAGE_FIELDS}),
            model=row["model"],
            prompt_version=row["prompt_version"],
            questions=tuple(question_from(q) for q in row["questions"]),
            answers=tuple(answer_from(a) for a in row["answers"]),
            proposal=proposal_from(row["proposal"]) if row["proposal"] else None,
            rejections=tuple(Rejection(r["code"], r["path"], r["detail"]) for r in row["rejections"]),
            candidate=candidate,
            reports=tuple(report_from(r) for r in row["reports"]),
            raw_output=RawOutput(sha256, row["raw_output_bytes"]) if sha256 else None,
            failure=_failure(row["failure"]),
            accepted=accepted,
            decision_reason=row["decision_reason"],
            history=tuple(_event(e) for e in row["history"]),
            completed_at=row["completed_at"],
            limitations=tuple(row["limitations"]),
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        InvalidArchitecture,
        InvalidAgentRequest,
    ) as error:  # a part that does not hold
        raise InvalidAgentRecord(details={"fields": [type(error).__name__]}) from error
