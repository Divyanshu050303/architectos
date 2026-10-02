"""The deterministic drift engine: compatibility first, then identity matching, differences and their
coverage-aware classification — one result citing both sides exactly, with the version of every rule.

Pure and bounded: it reads the baseline revision's content and a stored discovery result already
loaded, fetches and executes nothing, and changes neither. Identical inputs, mappings, policy and
versions give the identical result (and fingerprint).
"""

from collections.abc import Mapping

from core.domain.components.repository import ComponentCatalog
from core.domain.drift.analyses import DriftRequest, DriftResult
from core.domain.drift.ports import DriftInputs
from core.domain.drift.values import Compatibility
from core.domain.migrations.evidence import AnalysisEvidence

from . import classification, comparison, compatibility, impact, matching
from .compatibility import BaselineInput, ObservedInput

RULES = (compatibility.RULE, matching.RULE, comparison.RULE, classification.RULE, impact.RULE)


def _versions() -> dict[str, int]:
    return {name: int(version) for name, _, version in (r.partition("@") for r in RULES)}


class DeterministicDriftEngine:
    def __init__(self, catalog: ComponentCatalog | None = None) -> None:
        """``catalog``: which engines read a property, by component; None: no impact context."""
        self._catalog = catalog

    def analyze(
        self,
        request: DriftRequest,
        baseline: BaselineInput,
        observed: ObservedInput,
        mappings: Mapping[str, str] | None = None,
        evidence: tuple[AnalysisEvidence, ...] = (),
    ) -> DriftResult:
        """``evidence``: the other engines' stored analyses of the baseline's architecture, for
        context (``impact``); drift is detected without them."""
        assessment = compatibility.assess(baseline, observed, mappings)
        if assessment.status is Compatibility.INCOMPATIBLE:
            findings = classification.incompatibility(assessment)  # nothing else is compared
        else:
            matched = matching.match(baseline.ir, observed.result, mappings or {}, request.exclude)
            found = comparison.differences(baseline.ir, observed.result, matched)
            findings = classification.classify(found, assessment)
            if self._catalog is not None:
                findings = impact.contextualize(findings, baseline.ir, baseline.ref, self._catalog, evidence)
        return DriftResult(
            baseline.ref,
            observed.ref,
            assessment.checks,
            assessment.coverage,
            findings,
            assessment.warnings,
            _versions(),
            request.policy,
        )

    def run(self, inputs: DriftInputs) -> DriftResult:
        """The ``DriftEngine`` port: the comparison of exactly these inputs."""
        baseline = BaselineInput(
            inputs.baseline, inputs.baseline_ir, inputs.baseline_created_at, inputs.latest_revision,
            inputs.baseline_source,
        )  # fmt: skip
        observed = ObservedInput(inputs.observed, inputs.observed_result, inputs.observed_at)
        return self.analyze(inputs.request, baseline, observed, inputs.mappings, inputs.evidence)

    def versions(self) -> Mapping[str, int]:
        return _versions()
