"""An evolution analysis as stored and read back: everything but its candidates, which are read page by
page. What is needed to reproduce and explain it is kept: the request's inputs, the rule and engine
versions, the fingerprint, the summary, the goals, the findings, the evidence considered, the
assumptions and the limitations. ``result`` rebuilds the full result from the stored candidates and
refuses one that no longer matches its fingerprint."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from core.domain.engine_results import Evidence, Limitation, ModelSet

from .candidates import BaselineRef, Candidate, EvidenceRef
from .entities import EvolutionAnalysis
from .errors import InvalidEvolutionResult
from .goals import EvolutionGoal
from .results import EvolutionFinding, EvolutionResult


@dataclass(frozen=True, slots=True)
class EvolutionReport:
    analysis: EvolutionAnalysis  # without its result
    inputs: Mapping[str, Any]  # the request's inputs, the requirements read, policy, provider, currency
    model_set: ModelSet | None = None
    result_fingerprint: str | None = None
    summary: Mapping[str, Any] | None = None
    goals: tuple[EvolutionGoal, ...] = ()
    findings: tuple[EvolutionFinding, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    assumptions: tuple[Evidence, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    @classmethod
    def of(cls, analysis: EvolutionAnalysis, inputs: Mapping[str, Any]) -> EvolutionReport:
        """The report of an analysis just executed (its result still attached)."""
        result = analysis.result
        bare = replace(analysis, result=None)
        if result is None:
            return cls(bare, inputs)
        return cls(
            bare,
            inputs,
            result.model_set,
            result.fingerprint,
            result.summary(),
            result.goals,
            result.findings,
            result.evidence,
            result.assumptions,
            result.limitations,
        )

    @property
    def baseline(self) -> BaselineRef:
        a = self.analysis
        return BaselineRef(a.architecture_id, a.revision_number, a.revision_content_hash)

    def result(self, candidates: tuple[Candidate, ...]) -> EvolutionResult:
        """The full result, rebuilt from this report and its candidates; refused if it no longer
        matches what was stored."""
        if self.model_set is None or not self.goals:
            raise InvalidEvolutionResult(details={"fields": ["result"]})
        rebuilt = EvolutionResult(
            baseline=self.baseline,
            model_set=self.model_set,
            goals=self.goals,
            candidates=candidates,
            findings=self.findings,
            evidence=self.evidence,
            assumptions=self.assumptions,
            limitations=self.limitations,
        )
        if rebuilt.fingerprint != self.result_fingerprint:
            raise InvalidEvolutionResult(details={"fields": ["result_fingerprint"]})
        return rebuilt
