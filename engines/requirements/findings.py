"""Findings: everything the Requirements Engine has to say about an input besides its candidates.

Every step (validation, ambiguity, assumptions, conflicts, completeness) reports in this one shape,
so the analysis can be read, stored and compared uniformly:

- ``kind``      which step and what sort of finding (invalid, ambiguous, assumption, conflict, …)
- ``code``      a stable, machine-readable reason ("out_of_range", "user_count_kind_unspecified")
- ``severity``  blocking (architecture cannot proceed), warning (should be resolved), info
- ``message``   what is wrong, for people; ``suggestion`` how to resolve it
- where: the candidates concerned and/or the exact span of the input

A finding's key is deterministic, so the same input always produces the same findings, and a
finding can be referred to across re-runs.
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from core.domain.requirements.analysis import Severity
from core.domain.requirements.candidates import SourceSpan


class FindingKind(StrEnum):
    INVALID = "invalid"  # a requirement that breaks the rules (150 % availability)
    REJECTED = "rejected"  # a proposal the engine could not trust (span not in the input)
    DUPLICATE = "duplicate"  # the same requirement stated twice
    UNRESOLVED = "unresolved"  # requirement-like text that could not be read
    AMBIGUITY = "ambiguity"  # vague or underspecified ("fast", "high traffic")
    ASSUMPTION = "assumption"  # an interpretation the text did not state
    CONFLICT = "conflict"  # requirements no value can satisfy together
    CONSISTENCY = "consistency"  # one requirement strengthens or repeats another
    COMPLETENESS = "completeness"  # an area the project needs but nobody specified
    EXTRACTION = "extraction"  # how the analysis was produced (e.g. semantic extraction unavailable)


@dataclass(frozen=True, slots=True)
class Finding:
    kind: FindingKind
    code: str
    severity: Severity
    message: str
    candidate_keys: tuple[str, ...] = ()
    requirement_references: tuple[str, ...] = ()  # existing requirements concerned, e.g. "REQ-3@v2"
    span: SourceSpan | None = None
    field: str | None = None
    suggestion: str | None = None
    options: tuple[str, ...] = ()  # possible readings, for ambiguities and assumptions
    confidence: Decimal | None = None  # for assumptions: how safe the assumed reading is
    metric: str | None = None  # for conflicts and consistency

    @property
    def key(self) -> str:
        identity = [
            self.kind.value,
            self.code,
            list(self.candidate_keys),
            list(self.requirement_references),
            [self.span.start, self.span.end] if self.span else None,
            self.field,
        ]
        digest = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode())
        return "find_" + digest.hexdigest()[:16]

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.BLOCKING


def ordered(findings: list[Finding]) -> tuple[Finding, ...]:
    """Blocking first, then by position in the input, then by key: a stable, readable order."""
    rank = {Severity.BLOCKING: 0, Severity.WARNING: 1, Severity.INFO: 2}
    return tuple(
        sorted(
            dict.fromkeys(findings),
            key=lambda f: (rank[f.severity], f.span.start if f.span else -1, f.key),
        )
    )
