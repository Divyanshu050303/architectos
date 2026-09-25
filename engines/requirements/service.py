"""The Requirements Engine: orchestration only. Each step lives in its own module:

    extract → validate → ambiguity → assumptions → conflicts → completeness → analysis

It implements the domain's ``RequirementsAnalyzer`` port, so the use case that stores analyses
never depends on this package. Deterministic: the same input and the same existing requirements
always give the same result, finding keys and candidate keys included.

The result is JSON-ready and versioned (``result_schema``) because it is stored as is, append-only,
in the analysis record, and promotion later reads the candidates back from it.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from core.domain.requirements.analyses import AnalyzerOutput, input_sha256
from core.domain.requirements.analysis import Severity
from core.domain.requirements.entities import Requirement
from core.domain.requirements.normalization import canonical_data
from core.domain.requirements.value_objects import decimal_to_str

from .ambiguity import find_ambiguities
from .assumptions import find_assumptions
from .completeness import Completeness, assess
from .conflicts import Existing, find_conflicts
from .extractor import extract
from .findings import Finding, FindingKind, ordered
from .validation import Validated, validate

ENGINE_VERSION = "rules-1.0.0"
RESULT_SCHEMA = 1

# Where each kind of finding is listed in the result.
_GROUPS: dict[str, set[FindingKind]] = {
    "issues": {FindingKind.INVALID, FindingKind.REJECTED, FindingKind.DUPLICATE, FindingKind.UNRESOLVED},
    "ambiguities": {FindingKind.AMBIGUITY},
    "assumptions": {FindingKind.ASSUMPTION},
    "conflicts": {FindingKind.CONFLICT, FindingKind.CONSISTENCY},
    "completeness_findings": {FindingKind.COMPLETENESS},
}


def finding_dict(finding: Finding) -> dict[str, Any]:
    span = finding.span
    return {
        "key": finding.key,
        "kind": finding.kind.value,
        "code": finding.code,
        "severity": finding.severity.value,
        "message": finding.message,
        "suggestion": finding.suggestion,
        "field": finding.field,
        "metric": finding.metric,
        "options": list(finding.options),
        "confidence": decimal_to_str(finding.confidence) if finding.confidence is not None else None,
        "candidate_keys": list(finding.candidate_keys),
        "requirement_references": list(finding.requirement_references),
        "span": {"start": span.start, "end": span.end, "text": span.text} if span else None,
    }


def _completeness_dict(completeness: Completeness) -> dict[str, Any]:
    return {
        "status": completeness.status.value,
        "profiles": [{"name": p.name, "evidence": list(p.evidence)} for p in completeness.profiles],
        "importance": {area.value: level.name.lower() for area, level in completeness.importance.items()},
        "covered": [a.value for a in completeness.covered],
        "missing": [a.value for a in completeness.missing],
    }


def _confidence(validated: Validated) -> dict[str, str | None]:
    values = [c.confidence for c in validated.candidates]
    if not values:
        return {"lowest": None, "average": None}
    average = (sum(values, Decimal(0)) / len(values)).quantize(Decimal("0.001"))
    return {"lowest": decimal_to_str(min(values)), "average": decimal_to_str(average)}


def analyze(raw_input: str, existing: Sequence[Existing] = ()) -> dict[str, Any]:
    """The full, deterministic analysis of ``raw_input`` against ``existing`` requirements."""
    extraction = extract(raw_input)
    validated = validate(extraction)
    candidates = validated.candidates
    completeness = assess(raw_input, [c.content for c in candidates] + [e.content for e in existing])
    findings = ordered(
        [
            *validated.findings,
            *find_ambiguities(validated),
            *find_assumptions(extraction, validated),
            *find_conflicts(candidates, tuple(existing)),
            *completeness.findings,
        ]
    )
    blocking = [f for f in findings if f.severity is Severity.BLOCKING]
    result: dict[str, Any] = {
        "result_schema": RESULT_SCHEMA,
        "engine_version": ENGINE_VERSION,
        "input_sha256": input_sha256(raw_input),
        "candidates": [
            c.to_dict() | {"normalized_data": canonical_data(c.content.constraint)} for c in candidates
        ],
        "completeness": _completeness_dict(completeness),
        "confidence": _confidence(validated),
        "ready_for_architecture": not blocking,
        "blocking": [f.key for f in blocking],
        "counts": {
            "candidates": len(candidates),
            "blocking": len(blocking),
            "warnings": sum(f.severity is Severity.WARNING for f in findings),
            "info": sum(f.severity is Severity.INFO for f in findings),
        },
    }
    for group, kinds in _GROUPS.items():
        result[group] = [finding_dict(f) for f in findings if f.kind in kinds]
    return result


class RequirementsEngine:
    """The deterministic engine behind the domain's ``RequirementsAnalyzer`` port."""

    version = ENGINE_VERSION

    async def analyze(self, raw_input: str, existing: Sequence[Requirement]) -> AnalyzerOutput:
        result = analyze(raw_input, [Existing.of(r) for r in existing])
        return AnalyzerOutput(
            engine_version=ENGINE_VERSION,
            result=result,
            ready_for_architecture=result["ready_for_architecture"],
            candidate_count=result["counts"]["candidates"],
            blocking_count=result["counts"]["blocking"],
        )
