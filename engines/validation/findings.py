"""Building findings and verdicts from a rule's metadata, so every rule reports the same way."""

from collections.abc import Iterable

from core.domain.validation.results import Evidence, Finding, RequirementResult, Severity, Verdict

from .engine import RuleMeta


def finding(  # noqa: PLR0913 -- every Finding field, by keyword
    meta: RuleMeta,
    code: str,
    *,
    title: str,
    explanation: str,
    remediation: str,
    entity_ids: Iterable[str] = (),
    field_paths: Iterable[str] = (),
    expected: str | None = None,
    actual: str | None = None,
    evidence: Iterable[tuple[str, str]] = (),
    severity: Severity | None = None,
    blocking: bool = False,
    requirement_id: str | None = None,
    policy_rule: str | None = None,
) -> Finding:
    return Finding(
        rule_id=meta.id,
        rule_version=meta.version,
        code=code,
        severity=severity or meta.severity,
        category=meta.category,
        title=title,
        explanation=explanation,
        remediation=remediation,
        entity_ids=tuple(entity_ids),
        field_paths=tuple(field_paths),
        expected=expected,
        actual=actual,
        evidence=tuple(Evidence(label, value) for label, value in evidence),
        blocking=blocking,
        requirement_id=requirement_id,
        policy_rule=policy_rule,
    )


def verdict(
    meta: RuleMeta,
    *,
    requirement_id: str,
    reference: str,
    requirement_version: int,
    verdict: Verdict,
    reason: str,
    entity_ids: Iterable[str] = (),
    evidence: Iterable[tuple[str, str]] = (),
) -> RequirementResult:
    return RequirementResult(
        requirement_id=requirement_id,
        reference=reference,
        requirement_version=requirement_version,
        verdict=verdict,
        reason=reason,
        rule_id=meta.id,
        entity_ids=tuple(entity_ids),
        evidence=tuple(Evidence(label, value) for label, value in evidence),
    )
