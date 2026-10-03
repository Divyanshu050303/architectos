"""A stored diff and its explanation runs: JSON documents by column, read back into the same domain
objects.

Reading is strict — every part is rebuilt through its own constructor, so a stored record that no
longer satisfies the domain's rules is refused (``InvalidDiffRecord``), never half-read. Neither
architecture is stored (only each state's reference and content hash); nor the prompt, the context,
the retrieved text or the model's raw output (only that output's SHA-256 and size).
"""

import uuid
from collections.abc import Mapping
from typing import Any

from core.architecture_ir.diff import ChangeKind
from core.architecture_ir.errors import ElementType
from core.domain.architecture_agent.records import USAGE_FIELDS
from core.domain.architecture_agent.requests import AgentUsage
from core.domain.architecture_agent.results import EvidenceRef, Rejection
from core.domain.architecture_agent.runs import RawOutput
from core.domain.errors import DomainError

from .changes import Change, ChangeGroup, FieldDelta, SemanticDiff
from .diffs import ArchitectureDiff
from .errors import InvalidDiffRecord
from .explanations import (
    DiffExplanation,
    ExplanationRun,
    Grounding,
    GroupExplanation,
    RequirementExplanation,
    Statement,
)
from .impacts import DecisionImpact, EngineImpact, FindingDelta, MeasureDelta, RequirementImpact
from .references import ComparedState, DiffRequest, StateRef
from .values import (
    Basis,
    ChangeClass,
    ExplanationFailure,
    ExplanationStatus,
    FindingState,
    ImpactStatus,
    RequirementRelation,
    Sensitivity,
    StateKind,
    ValueType,
)

UNREADABLE = (KeyError, TypeError, ValueError, AttributeError, DomainError)

# --- writing ------------------------------------------------------------------------------------


def _side(prefix: str, state: ComparedState) -> dict[str, Any]:
    ref = state.ref
    return {
        f"{prefix}_kind": ref.kind.value,
        f"{prefix}_architecture_id": ref.architecture_id,
        f"{prefix}_revision_number": ref.revision_number,
        f"{prefix}_run_id": ref.run_id,
        f"{prefix}_content_hash": state.content_hash,
        f"{prefix}_label": state.label,
    }


def diff_document(diff: ArchitectureDiff) -> dict[str, Any]:
    """Every stored part of the diff, by column."""
    return {
        "id": diff.id,
        "project_id": diff.project_id,
        "requested_by_user_id": diff.requested_by_user_id,
        "compared_at": diff.created_at,
        **_side("base", diff.base),
        **_side("target", diff.target),
        "request": diff.request.to_dict(),
        "semantic": diff.semantic.to_dict(),
        "change_count": len(diff.semantic.changes),
        "counts": diff.semantic.counts(),
        "requirements": [r.to_dict() for r in diff.requirements],
        "decisions": [d.to_dict() for d in diff.decisions],
        "engines": [e.to_dict() for e in diff.engines],
        "warnings": list(diff.warnings),
        "unknowns": list(diff.unknowns),
    }


def explanation_document(project_id: uuid.UUID, run: ExplanationRun) -> dict[str, Any]:
    raw = run.raw_output
    return {
        "id": run.id,
        "diff_id": run.diff_id,
        "project_id": project_id,
        "requested_by_user_id": run.requested_by_user_id,
        "requested_at": run.requested_at,
        "status": run.status.value,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "usage": {k: getattr(run.usage, k) for k in USAGE_FIELDS},
        "raw_output_sha256": raw.sha256 if raw else None,
        "raw_output_bytes": raw.bytes if raw else None,
        "explanation": run.explanation.to_dict() if run.explanation else None,
        "evidence": [e.to_dict() for e in run.evidence],
        "failure": run.failure.value if run.failure else None,
        "rejections": [r.to_dict() for r in run.rejections],
        "limitations": list(run.limitations),
    }


# --- reading ------------------------------------------------------------------------------------


def _uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def state_ref(d: Mapping[str, Any]) -> StateRef:
    return StateRef(
        StateKind(d["kind"]), _uuid(d["architecture_id"]), d["revision_number"], _uuid(d["run_id"])
    )


def _request(d: Mapping[str, Any]) -> DiffRequest:
    scope = d["requirement_ids"]
    return DiffRequest(
        state_ref(d["base"]),
        state_ref(d["target"]),
        tuple(uuid.UUID(r) for r in scope) if scope is not None else None,
        _uuid(d["capacity_analysis_id"]),
        _uuid(d["cost_analysis_id"]),
        d["context"],
        d["explain"],
    )


def _compared(row: Mapping[str, Any], prefix: str) -> ComparedState:
    ref = state_ref(
        {
            "kind": row[f"{prefix}_kind"],
            "architecture_id": row[f"{prefix}_architecture_id"],
            "revision_number": row[f"{prefix}_revision_number"],
            "run_id": row[f"{prefix}_run_id"],
        }
    )
    return ComparedState(ref, row[f"{prefix}_content_hash"], row[f"{prefix}_label"])


def _field(d: Mapping[str, Any]) -> FieldDelta:
    return FieldDelta(
        d["path"],
        d["before"],
        d["after"],
        ValueType(d["before_type"]),
        ValueType(d["after_type"]),
        d["category"],
        tuple(ChangeClass(c) for c in d["classes"]),
        Sensitivity(d["sensitivity"]),
        d["unit"],
    )


def _change(d: Mapping[str, Any]) -> Change:
    ends = d["endpoints"]
    return Change(
        d["id"],
        ElementType(d["element"]),
        d["element_id"],
        ChangeKind(d["change"]),
        d["label"],
        d["kind"],
        tuple(ChangeClass(c) for c in d["classes"]),
        tuple(_field(f) for f in d["fields"]),
        d["renamed"],
        (ends[0], ends[1]) if ends else None,
    )


def _group(d: Mapping[str, Any]) -> ChangeGroup:
    return ChangeGroup(
        d["id"], d["rule"], d["title"], d["reason"], tuple(d["change_ids"]), tuple(d["element_ids"])
    )


def semantic_from(d: Mapping[str, Any]) -> SemanticDiff:
    return SemanticDiff(
        d["base_hash"],
        d["target_hash"],
        tuple(_change(c) for c in d["changes"]),
        tuple(_group(g) for g in d["groups"]),
        dict(d["versions"]),
        d["schema_version"],
    )


def _requirement(d: Mapping[str, Any]) -> RequirementImpact:
    return RequirementImpact(
        uuid.UUID(d["requirement_id"]),
        d["reference"],
        d["version"],
        d["title"],
        d["statement"],
        RequirementRelation(d["relation"]),
        tuple(d["element_ids"]),
        tuple(d["change_ids"]),
        d["base_verdict"],
        d["target_verdict"],
    )


def _decision(d: Mapping[str, Any]) -> DecisionImpact:
    return DecisionImpact(
        uuid.UUID(d["decision_id"]),
        d["reference"],
        d["title"],
        d["status"],
        tuple(d["element_ids"]),
        tuple(d["change_ids"]),
    )


def _engine(d: Mapping[str, Any]) -> EngineImpact:
    findings = tuple(
        FindingDelta(
            f["finding_id"], FindingState(f["state"]), f["severity"], f["title"], tuple(f["elements"])
        )
        for f in d["findings"]
    )
    measures = tuple(MeasureDelta(m["name"], m["before"], m["after"], m["unit"]) for m in d["measures"])
    return EngineImpact(
        d["engine"],
        ImpactStatus(d["status"]),
        dict(d["versions"]),
        findings,
        d["unchanged"],
        dict(d["base_summary"]),
        dict(d["target_summary"]),
        measures,
        tuple(d["limitations"]),
        d["error"],
    )


def diff_from(row: Mapping[str, Any]) -> ArchitectureDiff:
    """The diff a stored row describes (``InvalidDiffRecord`` for one that does not hold)."""
    try:
        return ArchitectureDiff(
            id=row["id"],
            project_id=row["project_id"],
            request=_request(row["request"]),
            base=_compared(row, "base"),
            target=_compared(row, "target"),
            semantic=semantic_from(row["semantic"]),
            requested_by_user_id=row["requested_by_user_id"],
            created_at=row["compared_at"],
            requirements=tuple(_requirement(r) for r in row["requirements"]),
            decisions=tuple(_decision(d) for d in row["decisions"]),
            engines=tuple(_engine(e) for e in row["engines"]),
            warnings=tuple(row["warnings"]),
            unknowns=tuple(row["unknowns"]),
        )
    except InvalidDiffRecord:
        raise
    except UNREADABLE as error:  # a part that does not hold
        raise InvalidDiffRecord(details={"fields": [type(error).__name__]}) from error


def _statement(d: Mapping[str, Any]) -> Statement:
    groundings = tuple(Grounding(Basis(g["basis"]), g["ref"]) for g in d["groundings"])
    return Statement(d["text"], groundings, d["inferred"])


def explanation_from_dict(d: Mapping[str, Any]) -> DiffExplanation:
    """An explanation in its ``to_dict`` shape — as stored, and as the model is asked to write it."""
    groups = tuple(
        GroupExplanation(
            g["group_id"],
            g["title"],
            _statement(g["explanation"]),
            tuple(_statement(c) for c in g["consequences"]),
            tuple(g["unknowns"]),
        )
        for g in d["groups"]
    )
    requirements = tuple(
        RequirementExplanation(r["reference"], _statement(r["explanation"])) for r in d["requirements"]
    )
    return DiffExplanation(
        _statement(d["summary"]),
        groups,
        tuple(_statement(s) for s in d["tradeoffs"]),
        requirements,
        tuple(_statement(s) for s in d["risks"]),
        tuple(_statement(s) for s in d["questions"]),
        tuple(d["unknowns"]),
    )


def explanation_run_from(row: Mapping[str, Any]) -> ExplanationRun:
    """The explanation run a stored row describes (``InvalidDiffRecord`` for one that does not hold)."""
    try:
        sha256 = row["raw_output_sha256"]
        return ExplanationRun(
            id=row["id"],
            diff_id=row["diff_id"],
            status=ExplanationStatus(row["status"]),
            requested_by_user_id=row["requested_by_user_id"],
            requested_at=row["requested_at"],
            model=row["model"],
            prompt_version=row["prompt_version"],
            usage=AgentUsage(**{k: row["usage"][k] for k in USAGE_FIELDS}),
            raw_output=RawOutput(sha256, row["raw_output_bytes"]) if sha256 else None,
            explanation=explanation_from_dict(row["explanation"]) if row["explanation"] else None,
            evidence=tuple(
                EvidenceRef(e["chunk_id"], e["source_id"], e["source_version"], e["reference"])
                for e in row["evidence"]
            ),
            failure=ExplanationFailure(row["failure"]) if row["failure"] else None,
            rejections=tuple(Rejection(r["code"], r["path"], r["detail"]) for r in row["rejections"]),
            limitations=tuple(row["limitations"]),
        )
    except InvalidDiffRecord:
        raise
    except UNREADABLE as error:
        raise InvalidDiffRecord(details={"fields": [type(error).__name__]}) from error
