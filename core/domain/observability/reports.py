"""An observability analysis as stored and read back: everything but its components and findings,
which are read page by page. What the result established is kept with the analysis: its status and
summary, requirement and policy checks, what could not run, and limitations."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from core.domain.engine_results import Limitation, ModelSet, Unsupported

from .analyses import ObservabilityAnalysis
from .results import CheckResult


@dataclass(frozen=True, slots=True)
class ObservabilityReport:
    analysis: ObservabilityAnalysis  # without its result
    inputs: Mapping[str, Any]  # the request's inputs, the policy snapshot and the requirements read
    analyzer_set: ModelSet | None = None
    context_fingerprint: str | None = None
    result_fingerprint: str | None = None
    summary: Mapping[str, Any] | None = None
    checks: tuple[CheckResult, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    @classmethod
    def of(cls, analysis: ObservabilityAnalysis, inputs: Mapping[str, Any]) -> ObservabilityReport:
        """The report of an analysis just executed (its result still attached)."""
        result = analysis.result
        bare = replace(analysis, result=None)
        if result is None:
            return cls(bare, inputs)
        return cls(
            bare,
            inputs,
            result.analyzer_set,
            result.context_fingerprint,
            result.fingerprint,
            result.summary(),
            result.checks,
            result.unsupported,
            result.limitations,
        )
