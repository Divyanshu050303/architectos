"""The deterministic drift engine: compatibility first, then identity matching, differences and their
coverage-aware classification — one result citing both sides exactly, with the version of every rule.

Pure and bounded: it reads the baseline revision's content and a stored discovery result already
loaded, fetches and executes nothing, and changes neither. Identical inputs, mappings, policy and
versions give the identical result (and fingerprint).
"""

from collections.abc import Mapping

from core.domain.drift.analyses import DriftRequest, DriftResult
from core.domain.drift.values import Compatibility

from . import classification, comparison, compatibility, matching
from .compatibility import BaselineInput, ObservedInput

RULES = (compatibility.RULE, matching.RULE, comparison.RULE, classification.RULE)


def _versions() -> dict[str, int]:
    return {name: int(version) for name, _, version in (r.partition("@") for r in RULES)}


class DeterministicDriftEngine:
    def analyze(
        self,
        request: DriftRequest,
        baseline: BaselineInput,
        observed: ObservedInput,
        mappings: Mapping[str, str] | None = None,
    ) -> DriftResult:
        assessment = compatibility.assess(baseline, observed, mappings)
        if assessment.status is Compatibility.INCOMPATIBLE:
            findings = classification.incompatibility(assessment)  # nothing else is compared
        else:
            matched = matching.match(baseline.ir, observed.result, mappings or {}, request.exclude)
            found = comparison.differences(baseline.ir, observed.result, matched)
            findings = classification.classify(found, assessment)
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

    def versions(self) -> Mapping[str, int]:
        return _versions()
