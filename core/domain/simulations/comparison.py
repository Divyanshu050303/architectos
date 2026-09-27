"""Comparing two simulations: their scenarios side by side, only where they are directly comparable.

Within one simulation, the baseline and the scenario are always comparable: the same run evaluates
both with the same models, inputs and pricing snapshot (``SimulationResult.deltas``). Two simulations
are compared **per analysis**, and only when that analysis ran in both with

- the same simulation engine and evaluator versions,
- the same engine model set, and
- the same baseline result (its fingerprint): the same revision content, workload, pricing snapshot
  and engine inputs — so the two scenarios differ from one common baseline.

Otherwise the analysis is **not comparable**, with the reason (``not_run``, ``different_engine``,
``different_models``, ``different_baseline``): the two outcomes are never set side by side as if they
were equivalent. For a comparable analysis, each metric is the first simulation's scenario value
against the second's, in one unit (a unit mismatch is ``unit_mismatch``; a value one simulation already
marked not comparable stays so); a metric one of them does not have is unknown on that side; the
difference and percentage follow ``Delta`` (no percentage of 0 or of an unknown). Nothing is explained
causally: the comparison states values, units and provenance, not why they differ.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .results import AnalysisRun, Delta, SimulationResult
from .values import AnalysisKind, RunState

RAN = frozenset({RunState.COMPLETED, RunState.PARTIAL})


@dataclass(frozen=True, slots=True)
class AnalysisComparison:
    analysis: AnalysisKind
    comparable: bool
    reason: str | None  # why not, when not comparable
    model_set: str | None  # the shared model set version, when comparable
    baseline_fingerprint: str | None  # the shared baseline result, when comparable

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis": self.analysis.value,
            "comparable": self.comparable,
            "reason": self.reason,
            "model_set": self.model_set,
            "baseline_fingerprint": self.baseline_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class SimulationComparison:
    first_fingerprint: str  # the two results compared
    second_fingerprint: str
    analyses: tuple[AnalysisComparison, ...]
    deltas: tuple[Delta, ...]  # the first scenario's value (as baseline) against the second's

    @property
    def comparable(self) -> bool:
        """Whether any analysis could be compared."""
        return any(a.comparable for a in self.analyses)

    def to_dict(self) -> dict[str, Any]:
        return {
            "first": self.first_fingerprint,
            "second": self.second_fingerprint,
            "comparable": self.comparable,
            "analyses": [a.to_dict() for a in self.analyses],
            "deltas": [d.to_dict() for d in self.deltas],
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


def _versions(result: SimulationResult) -> tuple[tuple[str, int], ...]:
    """The simulation engine's and the evaluators' versions (not the scenario types, which differ)."""
    models = result.engine_set.models
    return tuple(m for m in models if m[0] == "simulation" or m[0].startswith("evaluator."))


def _run(result: SimulationResult, analysis: AnalysisKind) -> AnalysisRun | None:
    return next((r for r in result.runs if r.analysis is analysis), None)


def _analysis(
    first: SimulationResult, second: SimulationResult, analysis: AnalysisKind
) -> AnalysisComparison:
    a, b = _run(first, analysis), _run(second, analysis)
    if a is None or b is None or a.state not in RAN or b.state not in RAN:
        return AnalysisComparison(analysis, False, "not_run", None, None)
    if _versions(first) != _versions(second):
        return AnalysisComparison(analysis, False, "different_engine", None, None)
    if a.model_set is None or a.model_set != b.model_set:
        return AnalysisComparison(analysis, False, "different_models", None, None)
    if a.baseline_fingerprint != b.baseline_fingerprint:
        return AnalysisComparison(analysis, False, "different_baseline", None, None)
    return AnalysisComparison(analysis, True, None, a.model_set.version, a.baseline_fingerprint)


def _pair(analysis: AnalysisKind, element: str, metric: str, a: Delta | None, b: Delta | None) -> Delta:
    known = a if a is not None else b
    assert known is not None  # noqa: S101 -- the metric is in one of them
    note = None
    if a is not None and b is not None and a.unit != b.unit:
        note = "unit_mismatch"
    elif (a is not None and a.note) or (b is not None and b.note):
        note = "not_comparable_in_a_simulation"
    first = a.scenario if a is not None else None
    second = b.scenario if b is not None else None
    return Delta(analysis, element, metric, known.unit, first, second, note)


def _metrics(first: SimulationResult, second: SimulationResult, analysis: AnalysisKind) -> list[Delta]:
    left = {(d.element_id, d.metric): d for d in first.deltas if d.analysis is analysis}
    right = {(d.element_id, d.metric): d for d in second.deltas if d.analysis is analysis}
    return [
        _pair(analysis, e, m, left.get((e, m)), right.get((e, m)))
        for e, m in sorted(left.keys() | right.keys())
    ]


def compare(first: SimulationResult, second: SimulationResult) -> SimulationComparison:
    """``second``'s scenario against ``first``'s, analysis by analysis, where directly comparable."""
    analyses = tuple(_analysis(first, second, a) for a in AnalysisKind)
    deltas = [d for c in analyses if c.comparable for d in _metrics(first, second, c.analysis)]
    return SimulationComparison(first.fingerprint, second.fingerprint, analyses, tuple(deltas))
