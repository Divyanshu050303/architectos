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
from .semantic import SemanticExtractor, SemanticOutcome, merge, needs_semantic
from .validation import Validated, validate

ENGINE_VERSION = "rules-1.0.0"
RESULT_SCHEMA = 1

# Where each kind of finding is listed in the result.
_GROUPS: dict[str, set[FindingKind]] = {
    "issues": {
        FindingKind.INVALID,
        FindingKind.REJECTED,
        FindingKind.DUPLICATE,
        FindingKind.UNRESOLVED,
        FindingKind.EXTRACTION,
    },
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


def _semantic_findings(outcome: SemanticOutcome | None) -> list[Finding]:
    if outcome is None:
        return []
    if outcome.failure is not None:
        return [
            Finding(
                FindingKind.EXTRACTION,
                outcome.failure,
                Severity.WARNING,
                "Semantic extraction was not available; only the deterministic rules read this text.",
                suggestion="Review unresolved statements by hand, or analyze again later.",
            )
        ]
    return [
        Finding(
            FindingKind.REJECTED,
            rejection.code,
            Severity.WARNING,
            f"A model proposal was dropped: {rejection.detail}.",
            suggestion="Restate the requirement explicitly if it matters.",
        )
        for rejection in outcome.rejections
    ]


def _semantic_summary(
    reason: str | None, outcome: SemanticOutcome | None, configured: bool
) -> dict[str, Any]:
    if outcome is None:
        return {"used": False, "reason": reason if configured else "not_configured"}
    return {
        "used": True,
        "reason": reason,
        "source": outcome.source,
        "prompt_version": outcome.prompt_version,
        "status": outcome.failure or "ok",
        "accepted": len(outcome.candidates),
        "rejected": len(outcome.rejections),
        "usage": outcome.usage,
    }


def engine_version(outcome: SemanticOutcome | None) -> str:
    if outcome is None or outcome.failure is not None:
        return ENGINE_VERSION
    return f"{ENGINE_VERSION}+{outcome.source}#{outcome.prompt_version}"


def analyze(
    raw_input: str,
    existing: Sequence[Existing] = (),
    semantic: SemanticOutcome | None = None,
    *,
    semantic_configured: bool = False,
) -> dict[str, Any]:
    """The full analysis of ``raw_input`` against ``existing`` requirements; deterministic for a
    given ``semantic`` outcome (the model's proposals, if it was asked)."""
    extraction = extract(raw_input)
    reason = needs_semantic(extraction)
    if semantic is not None and semantic.failure is None:
        extraction = merge(extraction, semantic)
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
            *_semantic_findings(semantic),
        ]
    )
    blocking = [f for f in findings if f.severity is Severity.BLOCKING]
    result: dict[str, Any] = {
        "result_schema": RESULT_SCHEMA,
        "engine_version": engine_version(semantic),
        "input_sha256": input_sha256(raw_input),
        "semantic": _semantic_summary(reason, semantic, semantic_configured),
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
    """The engine behind the domain's ``RequirementsAnalyzer`` port. With a ``semantic`` extractor,
    a language model is consulted only when the rules leave requirement-like text unread."""

    def __init__(self, semantic: SemanticExtractor | None = None) -> None:
        self._semantic = semantic

    @property
    def semantic_source(self) -> str | None:
        """``provider/model`` of the semantic extractor, or None when the engine is rules-only."""
        return self._semantic.source if self._semantic is not None else None

    async def analyze(self, raw_input: str, existing: Sequence[Requirement]) -> AnalyzerOutput:
        outcome = None
        if self._semantic is not None and needs_semantic(extract(raw_input)) is not None:
            outcome = await self._semantic.propose(raw_input)
        result = analyze(
            raw_input,
            [Existing.of(r) for r in existing],
            outcome,
            semantic_configured=self._semantic is not None,
        )
        return AnalyzerOutput(
            engine_version=result["engine_version"],
            result=result,
            ready_for_architecture=result["ready_for_architecture"],
            candidate_count=result["counts"]["candidates"],
            blocking_count=result["counts"]["blocking"],
        )
