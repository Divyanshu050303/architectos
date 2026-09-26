"""A reliability analysis as stored and read back: everything but its components and findings,
which are read page by page. What the result established is kept with the analysis: its status and
summary, request paths, objective verdicts, unsupported calculations and limitations."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from core.domain.engine_results import Limitation, ModelSet, Unsupported

from .analyses import ReliabilityAnalysis
from .results import ObjectiveResult, PathResult


@dataclass(frozen=True, slots=True)
class ReliabilityReport:
    analysis: ReliabilityAnalysis  # without its result
    inputs: Mapping[str, Any]  # the request's inputs, and the requirements it read
    model_set: ModelSet | None = None
    context_fingerprint: str | None = None
    result_fingerprint: str | None = None
    summary: Mapping[str, Any] | None = None
    paths: tuple[PathResult, ...] = ()
    objectives: tuple[ObjectiveResult, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    @classmethod
    def of(cls, analysis: ReliabilityAnalysis, inputs: Mapping[str, Any]) -> ReliabilityReport:
        """The report of an analysis just executed (its result still attached)."""
        result = analysis.result
        bare = replace(analysis, result=None)
        if result is None:
            return cls(bare, inputs)
        return cls(
            bare,
            inputs,
            result.model_set,
            result.context_fingerprint,
            result.fingerprint,
            result.summary(),
            result.paths,
            result.objectives,
            result.unsupported,
            result.limitations,
        )
