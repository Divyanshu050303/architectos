"""What a run produces besides the proposal: the candidate architecture (canonical IR, with what was
normalized and what it cites), why a proposal was refused, and the deterministic engines' reports.

A ``Candidate`` exists only when the proposal became valid canonical IR: every element carries
``llm_proposal`` provenance (never verified), every requirement it cites is one of the set's, and
every passage it cites was retrieved in this run. Deterministic normalizations are recorded.

An ``EngineReport`` is what an engine said about the candidate — evaluated, not evaluated (its inputs
are missing) or failed — with its findings and limitations as the engine stated them. The model never
writes one.
"""

from dataclasses import dataclass, field
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash

from .values import EngineStatus, check, code, count, items, text, texts

MAX_FINDINGS = 500
MAX_REJECTIONS = 100


@dataclass(frozen=True, slots=True)
class Rejection:
    """Why (part of) a proposal cannot become an architecture: ``code`` is stable, ``path`` locates it."""

    code: str
    path: str
    detail: str

    def __post_init__(self) -> None:
        check(
            [
                code(self.code, "rejection.code"),
                text(self.path, "rejection.path", 200),
                text(self.detail, "rejection.detail", 500),
            ]
        )

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """A passage the candidate cites — one retrieved in this run, with its citation."""

    chunk_id: str
    source_id: str
    source_version: int
    reference: str  # e.g. "Runbook (v2): Orders > Failover (lines 5-7)"

    def __post_init__(self) -> None:
        check(
            [
                text(self.chunk_id, "evidence.chunk_id", 64),
                text(self.source_id, "evidence.source_id", 64),
                count(self.source_version, "evidence.source_version", minimum=1),
                text(self.reference, "evidence.reference", 500),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "reference": self.reference,
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    ir: ArchitectureIR
    normalizations: tuple[str, ...] = ()  # what was changed deterministically, and why
    evidence: tuple[EvidenceRef, ...] = ()
    uncovered_requirements: tuple[str, ...] = ()  # requirements of the set no element traces to

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.ir, ArchitectureIR) else "candidate.ir",
                texts(self.normalizations, "candidate.normalizations", 500),
                items(self.evidence, EvidenceRef, "candidate.evidence", 200),
                texts(self.uncovered_requirements, "candidate.uncovered_requirements", 32),
            ]
        )

    @property
    def content_hash(self) -> str:
        """What a person reviews, and what acceptance must name."""
        return content_hash(self.ir)


@dataclass(frozen=True, slots=True)
class AgentFinding:
    """One finding an engine reported, normalized for review (the engine's own words)."""

    engine: str
    rule: str
    severity: str
    message: str
    elements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                code(self.engine, "finding.engine"),
                text(self.rule, "finding.rule", 128),
                code(self.severity, "finding.severity"),
                text(self.message, "finding.message", 2000),
                texts(self.elements, "finding.elements", 128),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "elements": list(self.elements),
        }


@dataclass(frozen=True, slots=True)
class EngineReport:
    engine: str  # validation, reliability, security, observability, capacity, cost, simulation
    status: EngineStatus
    versions: dict[str, Any] = field(default_factory=dict)
    findings: tuple[AgentFinding, ...] = ()
    summary: dict[str, Any] = field(default_factory=dict)  # the engine's own counts
    limitations: tuple[str, ...] = ()
    error: str | None = None  # a stable code when it failed

    def __post_init__(self) -> None:
        not_evaluated = self.status is EngineStatus.NOT_EVALUATED
        check(
            [
                code(self.engine, "report.engine"),
                None if isinstance(self.status, EngineStatus) else "report.status",
                items(self.findings, AgentFinding, "report.findings", MAX_FINDINGS),
                texts(self.limitations, "report.limitations"),
                code(self.error, "report.error", required=False),
                "report.error" if (self.status is EngineStatus.FAILED) != (self.error is not None) else None,
                "report.findings" if self.status is not EngineStatus.EVALUATED and self.findings else None,
                "report.limitations" if not_evaluated and not self.limitations else None,  # always says why
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "status": self.status.value,
            "versions": dict(self.versions),
            "findings": [f.to_dict() for f in self.findings],
            "summary": dict(self.summary),
            "limitations": list(self.limitations),
            "error": self.error,
        }
