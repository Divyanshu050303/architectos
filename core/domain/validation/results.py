"""What a validation produces: findings, requirement verdicts, rule failures and their summary.

Three different things are kept apart on purpose:

- a **finding**: the architecture breaks a rule (with the evidence);
- a **requirement verdict**: what the architecture's data establishes about one requirement
  (satisfied, violated, not verifiable, not applicable). "Not verifiable" is never a pass;
- a **rule failure**: a rule could not execute. It says nothing about the architecture and is
  never reported as a finding.

Everything here is immutable and ordered canonically, and ``ValidationResult.fingerprint`` hashes
the deterministic content only (no timestamps, no generated ids): the same architecture revision,
rule set and context always give the same fingerprint.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import InvalidFinding

RULE_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")  # e.g. "structure.disconnected-component"
CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")  # within a rule, e.g. "not_connected"
MAX_TEXT = 2000
MAX_REFERENCES = 200
MAX_EVIDENCE = 50


class Severity(StrEnum):
    """How much the finding matters, from most to least; not whether the engine ran."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return list(Severity).index(self)


class Category(StrEnum):
    STRUCTURE = "structure"  # the shape of the graph
    CONFIGURATION = "configuration"  # component and connection settings
    COMPLETENESS = "completeness"  # what the architecture does not say (unknown or missing values)
    POLICY = "policy"  # the project's architecture policy
    REQUIREMENTS = "requirements"  # traceability to, and evidence for, requirements


class Verdict(StrEnum):
    SATISFIED = "satisfied"  # explicit architecture evidence meets the requirement
    VIOLATED = "violated"  # explicit architecture evidence contradicts it
    NOT_VERIFIABLE = "not_verifiable"  # the architecture data cannot establish it either way
    NOT_APPLICABLE = "not_applicable"  # the requirement does not concern the architecture's content


@dataclass(frozen=True, slots=True)
class Evidence:
    """One observed fact behind a finding or verdict, e.g. ("region of api", "eu-west-1")."""

    label: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "value": self.value}


def _text_problem(value: object, field_name: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > MAX_TEXT:
        return field_name
    return None


def _positive(value: object, field_name: str) -> str | None:
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 1
    return None if ok else field_name


def _identifier(value: object, field_name: str) -> str | None:
    return None if isinstance(value, str) and RULE_ID.fullmatch(value) else field_name


def _references(values: object, field_name: str) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_REFERENCES:
        return field_name
    if not all(isinstance(v, str) and 0 < len(v) <= 256 for v in values):
        return field_name
    return None


def _evidence(values: object) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_EVIDENCE:
        return "evidence"
    if not all(
        isinstance(e, Evidence) and isinstance(e.label, str) and isinstance(e.value, str) for e in values
    ):
        return "evidence"
    return None


def _check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidFinding(details={"fields": found})


def _sorted_strings(value: object) -> object:
    if isinstance(value, list | tuple) and all(isinstance(v, str) for v in value):
        return tuple(sorted(set(value)))
    return value


def _key(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()
    return f"{prefix}_{digest[:20]}"


@dataclass(frozen=True, slots=True)
class Finding:
    """The architecture breaks a rule. ``id`` is derived from the content, so the same violation
    has the same id in every run."""

    rule_id: str
    rule_version: int
    code: str  # what exactly is wrong, stable within the rule, e.g. "not_connected"
    severity: Severity
    category: Category
    title: str
    explanation: str
    remediation: str
    entity_ids: tuple[str, ...] = ()  # nodes and connections involved
    field_paths: tuple[str, ...] = ()  # IR field paths, e.g. "configuration.tls"
    expected: str | None = None
    actual: str | None = None
    evidence: tuple[Evidence, ...] = ()
    blocking: bool = False
    requirement_id: str | None = None  # the requirement concerned, if any
    policy_rule: str | None = None  # the policy setting concerned, if any

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_ids", _sorted_strings(self.entity_ids))
        object.__setattr__(self, "field_paths", _sorted_strings(self.field_paths))
        if isinstance(self.evidence, list):
            object.__setattr__(self, "evidence", tuple(self.evidence))
        _check(
            [
                _identifier(self.rule_id, "rule_id"),
                _positive(self.rule_version, "rule_version"),
                None if isinstance(self.code, str) and CODE.fullmatch(self.code) else "code",
                None if isinstance(self.severity, Severity) else "severity",
                None if isinstance(self.category, Category) else "category",
                _text_problem(self.title, "title"),
                _text_problem(self.explanation, "explanation"),
                _text_problem(self.remediation, "remediation"),
                _references(self.entity_ids, "entity_ids"),
                _references(self.field_paths, "field_paths"),
                _text_problem(self.expected, "expected", required=False),
                _text_problem(self.actual, "actual", required=False),
                _evidence(self.evidence),
                None if isinstance(self.blocking, bool) else "blocking",
                _text_problem(self.requirement_id, "requirement_id", required=False),
                _text_problem(self.policy_rule, "policy_rule", required=False),
            ]
        )

    @property
    def id(self) -> str:
        return _key(
            "fnd",
            self.rule_id,
            self.rule_version,
            self.code,
            self.entity_ids,
            self.field_paths,
            self.requirement_id,
            self.policy_rule,
        )

    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.severity.rank,
            self.category.value,
            self.rule_id,
            self.entity_ids,
            self.field_paths,
            self.id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "code": self.code,
            "severity": self.severity.value,
            "category": self.category.value,
            "title": self.title,
            "explanation": self.explanation,
            "remediation": self.remediation,
            "entity_ids": list(self.entity_ids),
            "field_paths": list(self.field_paths),
            "expected": self.expected,
            "actual": self.actual,
            "evidence": [e.to_dict() for e in self.evidence],
            "blocking": self.blocking,
            "requirement_id": self.requirement_id,
            "policy_rule": self.policy_rule,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Finding:
        try:
            finding = cls(
                rule_id=data["rule_id"],
                rule_version=data["rule_version"],
                code=data["code"],
                severity=Severity(data["severity"]),
                category=Category(data["category"]),
                title=data["title"],
                explanation=data["explanation"],
                remediation=data["remediation"],
                entity_ids=tuple(data.get("entity_ids", ())),
                field_paths=tuple(data.get("field_paths", ())),
                expected=data.get("expected"),
                actual=data.get("actual"),
                evidence=tuple(Evidence(e["label"], e["value"]) for e in data.get("evidence", ())),
                blocking=data.get("blocking", False),
                requirement_id=data.get("requirement_id"),
                policy_rule=data.get("policy_rule"),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidFinding(details={"fields": [type(error).__name__]}) from None
        if "id" in data and data["id"] != finding.id:
            raise InvalidFinding(details={"fields": ["id"]})  # a tampered or corrupted record
        return finding


@dataclass(frozen=True, slots=True)
class RequirementResult:
    """What the architecture's data establishes about one requirement (at one version)."""

    requirement_id: str
    reference: str  # e.g. "REQ-12"
    requirement_version: int
    verdict: Verdict
    reason: str
    rule_id: str
    entity_ids: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_ids", _sorted_strings(self.entity_ids))
        if isinstance(self.evidence, list):
            object.__setattr__(self, "evidence", tuple(self.evidence))
        _check(
            [
                _text_problem(self.requirement_id, "requirement_id"),
                _text_problem(self.reference, "reference"),
                _positive(self.requirement_version, "requirement_version"),
                None if isinstance(self.verdict, Verdict) else "verdict",
                _text_problem(self.reason, "reason"),
                _identifier(self.rule_id, "rule_id"),
                _references(self.entity_ids, "entity_ids"),
                _evidence(self.evidence),
            ]
        )

    def sort_key(self) -> tuple[Any, ...]:
        return (self.reference, self.requirement_id, self.rule_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "reference": self.reference,
            "requirement_version": self.requirement_version,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "entity_ids": list(self.entity_ids),
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RequirementResult:
        try:
            return cls(
                requirement_id=data["requirement_id"],
                reference=data["reference"],
                requirement_version=data["requirement_version"],
                verdict=Verdict(data["verdict"]),
                reason=data["reason"],
                rule_id=data["rule_id"],
                entity_ids=tuple(data.get("entity_ids", ())),
                evidence=tuple(Evidence(e["label"], e["value"]) for e in data.get("evidence", ())),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidFinding(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class RuleFailure:
    """A rule could not execute: an engine problem, not an architecture problem. ``error`` is a
    stable code and ``message`` a safe sentence, never a stack trace."""

    rule_id: str
    rule_version: int
    error: str  # e.g. "unexpected_error"
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "error": self.error,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RuleFailure:
        return cls(data["rule_id"], data["rule_version"], data["error"], data["message"])


@dataclass(frozen=True, slots=True)
class Summary:
    """Always derived from the findings and verdicts (``summarize``), never kept separately."""

    total: int
    by_severity: Mapping[str, int]
    by_category: Mapping[str, int]
    blocking: int
    requirements: Mapping[str, int]  # count per verdict
    rule_failures: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_severity": dict(self.by_severity),
            "by_category": dict(self.by_category),
            "blocking": self.blocking,
            "requirements": dict(self.requirements),
            "rule_failures": self.rule_failures,
        }


def summarize(
    findings: Iterable[Finding], requirement_results: Iterable[RequirementResult] = (), failures: int = 0
) -> Summary:
    found = list(findings)
    severities = Counter(f.severity.value for f in found)
    categories = Counter(f.category.value for f in found)
    verdicts = Counter(r.verdict.value for r in requirement_results)
    return Summary(
        total=len(found),
        by_severity={s.value: severities.get(s.value, 0) for s in Severity},
        by_category={c.value: categories.get(c.value, 0) for c in Category},
        blocking=sum(f.blocking for f in found),
        requirements={v.value: verdicts.get(v.value, 0) for v in Verdict},
        rule_failures=failures,
    )


@dataclass(frozen=True, slots=True)
class RuleSet:
    """Which rules ran: a stable id (the profile) and a version derived from the rules' ids and
    versions, so a changed rule changes the rule-set version."""

    id: str  # e.g. "default"
    version: str
    rules: tuple[tuple[str, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version, "rules": [list(r) for r in self.rules]}

    @classmethod
    def of(cls, profile: str, rules: Iterable[tuple[str, int]]) -> RuleSet:
        ordered = tuple(sorted(set(rules)))
        version = hashlib.sha256(json.dumps(ordered).encode()).hexdigest()[:16]
        return cls(profile, version, ordered)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """The engine's output for one architecture revision, rule set and context."""

    rule_set: RuleSet
    context_fingerprint: str  # identifies the inputs besides the IR (requirements, policy, config)
    findings: tuple[Finding, ...] = ()
    requirement_results: tuple[RequirementResult, ...] = ()
    failures: tuple[RuleFailure, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "findings", tuple(sorted(dict.fromkeys(self.findings), key=Finding.sort_key))
        )
        object.__setattr__(
            self,
            "requirement_results",
            tuple(sorted(self.requirement_results, key=RequirementResult.sort_key)),
        )
        object.__setattr__(
            self, "failures", tuple(sorted(self.failures, key=lambda f: (f.rule_id, f.rule_version, f.error)))
        )

    @property
    def summary(self) -> Summary:
        return summarize(self.findings, self.requirement_results, len(self.failures))

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the deterministic content: equal inputs, equal fingerprint."""
        document = {
            "rule_set": self.rule_set.to_dict(),
            "context": self.context_fingerprint,
            "findings": [f.to_dict() for f in self.findings],
            "requirements": [r.to_dict() for r in self.requirement_results],
            "failures": [f.to_dict() for f in self.failures],
        }
        return hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
