"""The other engines' stored analyses, as a migration plan reads them.

A plan reuses each engine's own stored analysis through the evolution contract (``StoredAnalysis``,
built by ``core.domain.evolution.evidence``) — never recomputing it. Each analysis keeps its id,
the revision and content hash it analyzed and its model version, so the plan can tell whether it is
of the plan's source, of its target, or of something else (stale).

**Coverage** states, per engine and per side (source and target), what the plan rests on: a current
analysis, only stale ones, none, or a dimension no engine can analyze for this transition (a
simulation compares scenarios of one revision, not a source and a target; the engines analyze
revisions, not a candidate's overlay — whose own evidence the candidate cites).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from core.domain.evolution.evidence import StoredAnalysis
from core.domain.evolution.values import EvidenceSource

from .values import check, references, text

# The engines a migration plan reads, in order.
DIMENSIONS = (
    EvidenceSource.VALIDATION,
    EvidenceSource.CAPACITY,
    EvidenceSource.COST,
    EvidenceSource.RELIABILITY,
    EvidenceSource.SECURITY,
    EvidenceSource.OBSERVABILITY,
    EvidenceSource.SIMULATION,
)


class Side(StrEnum):
    SOURCE = "source"
    TARGET = "target"


class CoverageState(StrEnum):
    CURRENT = "current"  # an analysis of this side's exact content
    STALE = "stale"  # only analyses of other revisions or content: reported, never used
    MISSING = "missing"  # no analysis
    UNSUPPORTED = "unsupported"  # no engine analyzes this dimension for this side


@dataclass(frozen=True, slots=True)
class AnalysisEvidence:
    """One stored analysis with what the plan needs besides the evolution contract's view: its
    stored inputs (the pricing snapshot, the workload) and summary (validation's counts)."""

    stored: StoredAnalysis
    inputs: Mapping[str, Any] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvidenceCoverage:
    source: EvidenceSource
    side: Side
    state: CoverageState
    analysis_ids: tuple[str, ...] = ()  # every analysis considered for this side
    note: str | None = None

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.source, EvidenceSource) else "coverage.source",
                None if isinstance(self.side, Side) else "coverage.side",
                None if isinstance(self.state, CoverageState) else "coverage.state",
                references(self.analysis_ids, "coverage.analysis_ids"),
                text(self.note, "coverage.note", required=False),
                "coverage.analysis_ids"
                if self.state in {CoverageState.CURRENT, CoverageState.STALE} and not self.analysis_ids
                else None,
            ]
        )

    @property
    def key(self) -> tuple[str, str]:
        return (self.source.value, self.side.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "side": self.side.value,
            "state": self.state.value,
            "analysis_ids": list(self.analysis_ids),
            "note": self.note,
        }
