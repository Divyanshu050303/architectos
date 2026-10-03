"""A workflow candidate: one architecture the workflow produced, kept forever with how it came to be.

- **Lineage.** The first candidate is the agent's (``ordinal`` 1, no parent). Every improvement names
  its ``parent`` and the ``trigger`` — the finding of the parent it answers — and its origin: a
  deterministic evolution ``rule`` (with the rule's id) or the agent again (``agent_revision``).
- **Immutable content.** The architecture is canonical IR, checked against its content hash; nothing
  replaces it. Analyses are added as engine reports; only the status moves.
- **Status** follows validation: a candidate validation blocks is ``rejected`` (kept, never deleted);
  one it does not is ``validated``. An improvement can ``supersede`` its parent in the review package;
  a person's approval makes the selected one ``accepted``.

    generated ─▶ validated ─▶ selected_for_review ─▶ accepted
        │            └──────▶ superseded ◀────────┘
        └▶ rejected
"""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain.architecture_agent.results import EngineReport, EvidenceRef
from core.domain.architecture_agent.values import EngineStatus

from .errors import InvalidWorkflowTransition
from .values import FINGERPRINT, CandidateOrigin, CandidateStatus, check, code, count, items, text, texts

C = CandidateStatus
MOVES: dict[CandidateStatus, frozenset[CandidateStatus]] = {
    C.GENERATED: frozenset({C.VALIDATED, C.REJECTED}),
    C.VALIDATED: frozenset({C.SELECTED_FOR_REVIEW, C.SUPERSEDED}),
    C.SELECTED_FOR_REVIEW: frozenset({C.ACCEPTED, C.SUPERSEDED}),
}
VALIDATED = frozenset({C.VALIDATED, C.SELECTED_FOR_REVIEW, C.ACCEPTED, C.SUPERSEDED})
MAX_REPORTS = 10


def blocking_findings(reports: tuple[EngineReport, ...]) -> int | None:
    """Validation's blocking findings, or None when validation was not evaluated (unknown, never 0)."""
    for report in reports:
        if report.engine == "validation" and report.status is EngineStatus.EVALUATED:
            blocking = report.summary.get("blocking")
            return blocking if isinstance(blocking, int) and not isinstance(blocking, bool) else None
    return None


@dataclass(frozen=True, slots=True)
class FindingRef:
    """The finding of a parent candidate an improvement answers: its engine and its stable id."""

    engine: str
    finding_id: str  # the engine's stable id or rule (e.g. "SYS005", "capacity:db:connections")
    severity: str
    elements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                code(self.engine, "trigger.engine"),
                text(self.finding_id, "trigger.finding_id", 256),
                code(self.severity, "trigger.severity"),
                texts(self.elements, "trigger.elements", 128),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "finding_id": self.finding_id,
            "severity": self.severity,
            "elements": list(self.elements),
        }


@dataclass(frozen=True, slots=True)
class WorkflowCandidate:
    id: uuid.UUID
    workflow_id: uuid.UUID
    ordinal: int  # 1, 2, … in creation order
    origin: CandidateOrigin
    reason: str  # why it was created, in the controller's words
    ir: ArchitectureIR
    content_hash: str
    created_at: datetime
    parent_id: uuid.UUID | None = None
    trigger: FindingRef | None = None
    rule: str | None = None  # the evolution rule that proposed it
    agent_run_id: uuid.UUID | None = None  # the agent run that proposed it
    status: CandidateStatus = CandidateStatus.GENERATED
    reports: tuple[EngineReport, ...] = ()  # what the engines found in it
    assumptions: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()  # the proposer's stated reasons (a model's or a rule's), unverified
    evidence: tuple[EvidenceRef, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        origin = self.origin
        first = self.ordinal == 1
        check(
            [
                None if isinstance(self.id, uuid.UUID) else "candidate.id",
                count(self.ordinal, "candidate.ordinal", minimum=1),
                None if isinstance(origin, CandidateOrigin) else "candidate.origin",
                text(self.reason, "candidate.reason", 500),
                None if isinstance(self.ir, ArchitectureIR) else "candidate.ir",
                None
                if isinstance(self.content_hash, str) and FINGERPRINT.fullmatch(self.content_hash)
                else "candidate.content_hash",
                None if isinstance(self.status, CandidateStatus) else "candidate.status",
                # lineage: the first is the agent's and has no parent; every other one has a parent
                "candidate.parent_id" if first != (self.parent_id is None) else None,
                "candidate.origin" if first and origin is not CandidateOrigin.AGENT else None,
                "candidate.origin" if not first and origin is CandidateOrigin.AGENT else None,
                "candidate.rule" if (origin is CandidateOrigin.RULE) != (self.rule is not None) else None,
                code(self.rule, "candidate.rule", required=False),
                "candidate.trigger" if origin is CandidateOrigin.RULE and self.trigger is None else None,
                None if self.trigger is None or isinstance(self.trigger, FindingRef) else "candidate.trigger",
                items(self.reports, EngineReport, "candidate.reports", MAX_REPORTS),
                texts(self.assumptions, "candidate.assumptions", 500),
                texts(self.rationale, "candidate.rationale", 1000),
                items(self.evidence, EvidenceRef, "candidate.evidence", 100),
                texts(self.limitations, "candidate.limitations", 500),
            ]
        )
        if isinstance(self.ir, ArchitectureIR) and content_hash(self.ir) != self.content_hash:
            check(["candidate.content_hash"])  # never another architecture than was produced
        blocking = blocking_findings(self.reports)
        check(
            [
                "candidate.status" if self.status in VALIDATED and blocking != 0 else None,
                "candidate.status" if self.status is C.REJECTED and not blocking else None,
            ]
        )

    @property
    def blocking(self) -> int | None:
        return blocking_findings(self.reports)

    def report(self, engine: str) -> EngineReport | None:
        return next((r for r in self.reports if r.engine == engine), None)

    def with_report(self, report: EngineReport) -> WorkflowCandidate:
        """An engine's report added (or replaced, for the same engine). The architecture never changes."""
        kept = tuple(r for r in self.reports if r.engine != report.engine)
        return replace(self, reports=(*kept, report))

    def moved(self, status: CandidateStatus) -> WorkflowCandidate:
        if status not in MOVES.get(self.status, frozenset()):
            raise InvalidWorkflowTransition(details={"from": self.status.value, "to": status.value})
        return replace(self, status=status)

    def validated(self) -> WorkflowCandidate:
        """Validated or rejected, as validation's report says; refused before validation evaluated it."""
        blocking = self.blocking
        if blocking is None:
            raise InvalidWorkflowTransition(details={"from": self.status.value, "to": C.VALIDATED.value})
        return self.moved(C.VALIDATED if blocking == 0 else C.REJECTED)
