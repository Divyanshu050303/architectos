"""What an observability analysis produces: each component's coverage per dimension, findings for
engineering review, and requirement and policy checks — never a score, never a claim that telemetry
works in production.

- A **finding**'s type fixes its category and its **basis**, kept apart: ``control_gap`` (the model
  declares an observability capability off where it matters), ``potential_risk`` (the modeled facts
  could hide or leak something), ``violation`` (a requirement or policy is contradicted by modeled
  evidence), ``not_evaluable`` (the model does not say enough). Severity follows validation's scale;
  certainty is ``modeled`` or ``candidate`` (inferred or proposed facts). Findings about one signal
  carry its dimension (part of their identity), requirement and policy findings the check they report.
- A **check** is the verdict for one requirement or policy rule (``core/domain/checks.py``):
  satisfied or violated by modeled evidence only, never a pass on missing evidence. An objective
  being measurable is never the objective being met: no attainment is computed.
- The **summary** counts coverage states per dimension, over every component in scope and over the
  components declared critical; nothing is a percentage.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from core.domain.capacity.results import Certainty
from core.domain.checks import MAX_ELEMENTS, Check, evidence_problem, ids_problem
from core.domain.checks import CheckSource as CheckSource  # noqa: PLC0414 -- re-exported: the contract
from core.domain.engine_results import (
    MAX_ID,
    Evidence,
    Limitation,
    ModelSet,
    Unsupported,
    read_evidence,
    text_problem,
)
from core.domain.engine_results import FindingBasis as FindingBasis  # noqa: PLC0414 -- re-exported, shared
from core.domain.validation.results import Severity, Verdict

from .errors import InvalidObservabilityResult
from .values import CoverageState, Dimension

FINDING_ID = re.compile(r"^obs_[0-9a-f]{16}$")

__all__ = ["MAX_ELEMENTS"]


def _check(problems: list[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidObservabilityResult(details={"fields": found})


def _sorted(data: object, name: str) -> None:
    value = getattr(data, name)
    if isinstance(value, tuple):
        object.__setattr__(data, name, tuple(sorted(set(value))))


class ObservabilityStatus(StrEnum):
    COMPLETED = "completed"  # every applicable dimension of every component is declared, every analyzer ran
    PARTIAL = "partial"  # some is declared, some is not, or an analyzer could not run
    INSUFFICIENT_INPUT = "insufficient_input"  # components exist, none declares any observability
    UNSUPPORTED = "unsupported"  # nothing in scope to analyze
    FAILED = "failed"  # the engine could not produce a result


class FindingCategory(StrEnum):
    LOGGING = "logging"
    METRICS = "metrics"
    TRACING = "tracing"
    HEALTH_CHECKS = "health_checks"
    ALERTING = "alerting"
    COLLECTION = "collection"  # telemetry paths to an observability component
    CRITICALITY = "criticality"
    REQUIREMENT = "requirement"
    POLICY = "policy"


class FindingType(StrEnum):
    CRITICALITY_NOT_MODELED = "criticality_not_modeled"
    # logging
    LOGS_ABSENT = "logs_absent"
    LOGS_NOT_MODELED = "logs_not_modeled"
    SENSITIVE_DATA_IN_LOGS = "sensitive_data_in_logs"
    # metrics
    METRICS_ABSENT = "metrics_absent"
    METRICS_NOT_MODELED = "metrics_not_modeled"
    # tracing
    TRACES_ABSENT = "traces_absent"
    TRACES_NOT_MODELED = "traces_not_modeled"
    PROPAGATION_BROKEN = "propagation_broken"
    PROPAGATION_NOT_MODELED = "propagation_not_modeled"
    # collection
    TELEMETRY_NOT_COLLECTED = "telemetry_not_collected"
    # health checks
    HEALTH_CHECK_ABSENT = "health_check_absent"
    HEALTH_CHECK_NOT_MODELED = "health_check_not_modeled"
    HEALTH_CHECK_UNCONSUMED = "health_check_unconsumed"
    # alerting
    ALERTS_ABSENT = "alerts_absent"
    ALERTS_NOT_MODELED = "alerts_not_modeled"
    ALERT_WITHOUT_SIGNAL = "alert_without_signal"
    ALERT_DELIVERY_NOT_MODELED = "alert_delivery_not_modeled"
    # requirements and policy
    REQUIREMENT_VIOLATED = "requirement_violated"
    REQUIREMENT_NOT_EVALUABLE = "requirement_not_evaluable"
    POLICY_VIOLATED = "policy_violated"
    POLICY_NOT_EVALUABLE = "policy_not_evaluable"


_B, _C, _T = FindingBasis, FindingCategory, FindingType
# Each type's category and basis: fixed, so the four kinds of finding are never mixed up.
TYPES: dict[FindingType, tuple[FindingCategory, FindingBasis]] = {
    _T.CRITICALITY_NOT_MODELED: (_C.CRITICALITY, _B.NOT_EVALUABLE),
    _T.LOGS_ABSENT: (_C.LOGGING, _B.CONTROL_GAP),
    _T.LOGS_NOT_MODELED: (_C.LOGGING, _B.NOT_EVALUABLE),
    _T.SENSITIVE_DATA_IN_LOGS: (_C.LOGGING, _B.POTENTIAL_RISK),
    _T.METRICS_ABSENT: (_C.METRICS, _B.CONTROL_GAP),
    _T.METRICS_NOT_MODELED: (_C.METRICS, _B.NOT_EVALUABLE),
    _T.TRACES_ABSENT: (_C.TRACING, _B.CONTROL_GAP),
    _T.TRACES_NOT_MODELED: (_C.TRACING, _B.NOT_EVALUABLE),
    _T.PROPAGATION_BROKEN: (_C.TRACING, _B.CONTROL_GAP),
    _T.PROPAGATION_NOT_MODELED: (_C.TRACING, _B.NOT_EVALUABLE),
    _T.TELEMETRY_NOT_COLLECTED: (_C.COLLECTION, _B.NOT_EVALUABLE),
    _T.HEALTH_CHECK_ABSENT: (_C.HEALTH_CHECKS, _B.CONTROL_GAP),
    _T.HEALTH_CHECK_NOT_MODELED: (_C.HEALTH_CHECKS, _B.NOT_EVALUABLE),
    _T.HEALTH_CHECK_UNCONSUMED: (_C.HEALTH_CHECKS, _B.NOT_EVALUABLE),
    _T.ALERTS_ABSENT: (_C.ALERTING, _B.CONTROL_GAP),
    _T.ALERTS_NOT_MODELED: (_C.ALERTING, _B.NOT_EVALUABLE),
    _T.ALERT_WITHOUT_SIGNAL: (_C.ALERTING, _B.CONTROL_GAP),
    _T.ALERT_DELIVERY_NOT_MODELED: (_C.ALERTING, _B.NOT_EVALUABLE),
    _T.REQUIREMENT_VIOLATED: (_C.REQUIREMENT, _B.VIOLATION),
    _T.REQUIREMENT_NOT_EVALUABLE: (_C.REQUIREMENT, _B.NOT_EVALUABLE),
    _T.POLICY_VIOLATED: (_C.POLICY, _B.VIOLATION),
    _T.POLICY_NOT_EVALUABLE: (_C.POLICY, _B.NOT_EVALUABLE),
}
assert set(TYPES) == set(FindingType)  # noqa: S101 - every type has its category and basis


@dataclass(frozen=True, slots=True)
class ObservabilityFinding:
    type: FindingType
    severity: Severity
    certainty: Certainty
    title: str
    explanation: str  # what was detected, why it matters, and what it does not establish
    recommendation: str  # what to investigate or improve, for review; never applied automatically
    node_ids: tuple[str, ...] = ()
    connection_ids: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    assumptions: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()  # what the model would need to declare to decide
    analyzer_id: str | None = None
    analyzer_version: int | None = None
    dimension: Dimension | None = None  # the signal it is about, if one (part of the identity)
    requirement_id: str | None = None  # (part of the identity)
    policy_rule: str | None = None  # (part of the identity)
    check_key: str | None = None  # the check a requirement or policy finding reports (identity)

    def __post_init__(self) -> None:
        for name in ("node_ids", "connection_ids", "missing", "assumptions"):
            _sorted(self, name)
        category = TYPES[self.type][0] if isinstance(self.type, FindingType) else None
        checked = category in {FindingCategory.REQUIREMENT, FindingCategory.POLICY}
        _check(
            [
                None if isinstance(self.type, FindingType) else "type",
                None if isinstance(self.severity, Severity) else "severity",
                None if isinstance(self.certainty, Certainty) else "certainty",
                text_problem(self.title, "title"),
                text_problem(self.explanation, "explanation"),
                text_problem(self.recommendation, "recommendation"),
                ids_problem(self.node_ids, "node_ids"),
                ids_problem(self.connection_ids, "connection_ids"),
                None if self.node_ids or self.connection_ids else "node_ids",
                evidence_problem(self.evidence),
                ids_problem(self.assumptions, "assumptions"),
                ids_problem(self.missing, "missing"),
                None if (self.analyzer_id is None) == (self.analyzer_version is None) else "analyzer_version",
                None if self.dimension is None or isinstance(self.dimension, Dimension) else "dimension",
                None
                if (self.requirement_id is not None) == (category is FindingCategory.REQUIREMENT)
                else "requirement_id",
                None
                if (self.policy_rule is not None) == (category is FindingCategory.POLICY)
                else "policy_rule",
                None if (self.check_key is not None) == checked else "check_key",
                text_problem(self.requirement_id, "requirement_id", required=False),
                text_problem(self.policy_rule, "policy_rule", required=False),
                text_problem(self.check_key, "check_key", required=False),
            ]
        )

    @property
    def category(self) -> FindingCategory:
        return TYPES[self.type][0]

    @property
    def basis(self) -> FindingBasis:
        return TYPES[self.type][1]

    @property
    def id(self) -> str:
        """Stable: the same type about the same elements (and dimension, requirement, policy rule or
        check) has the same id in every analysis."""
        parts: list[Any] = [self.type.value, list(self.node_ids), list(self.connection_ids)]
        for extra in (self.dimension, self.requirement_id, self.policy_rule, self.check_key):
            if extra is not None:
                parts.append(str(extra))
        return "obs_" + hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]

    def sort_key(self) -> tuple[int, str, str]:
        return (list(Severity).index(self.severity), self.type.value, self.id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "category": self.category.value,
            "basis": self.basis.value,
            "severity": self.severity.value,
            "certainty": self.certainty.value,
            "title": self.title,
            "explanation": self.explanation,
            "recommendation": self.recommendation,
            "node_ids": list(self.node_ids),
            "connection_ids": list(self.connection_ids),
            "evidence": [e.to_dict() for e in self.evidence],
            "assumptions": list(self.assumptions),
            "missing": list(self.missing),
            "analyzer_id": self.analyzer_id,
            "analyzer_version": self.analyzer_version,
            "dimension": self.dimension.value if self.dimension is not None else None,
            "requirement_id": self.requirement_id,
            "policy_rule": self.policy_rule,
            "check_key": self.check_key,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            dimension = data.get("dimension")
            return cls(
                type=FindingType(data["type"]),
                severity=Severity(data["severity"]),
                certainty=Certainty(data["certainty"]),
                title=data["title"],
                explanation=data["explanation"],
                recommendation=data["recommendation"],
                node_ids=tuple(data.get("node_ids") or ()),
                connection_ids=tuple(data.get("connection_ids") or ()),
                evidence=read_evidence(data.get("evidence")),
                assumptions=tuple(data.get("assumptions") or ()),
                missing=tuple(data.get("missing") or ()),
                analyzer_id=data.get("analyzer_id"),
                analyzer_version=data.get("analyzer_version"),
                dimension=Dimension(dimension) if dimension is not None else None,
                requirement_id=data.get("requirement_id"),
                policy_rule=data.get("policy_rule"),
                check_key=data.get("check_key"),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidObservabilityResult(details={"fields": [type(error).__name__]}) from None


class Condition(StrEnum):
    """What an observability requirement or policy rule is checked as: a fixed condition on what the
    architecture declares (never on runtime telemetry)."""

    LOGS_ON_CRITICAL = "logs_on_critical"
    METRICS_ON_CRITICAL = "metrics_on_critical"  # with the required kinds
    TRACES_ON_CRITICAL = "traces_on_critical"
    PROPAGATION_ON_CRITICAL = "propagation_on_critical"
    HEALTH_CHECKS_ON_CRITICAL = "health_checks_on_critical"
    ALERTING_ON_CRITICAL = "alerting_on_critical"
    STRUCTURED_LOGS = "structured_logs"
    CORRELATION_IDS = "correlation_ids"
    OWNERSHIP = "ownership"
    TELEMETRY_RETENTION = "telemetry_retention"
    TELEMETRY_COLLECTED = "telemetry_collected"
    OBJECTIVE_MEASURABLE = "objective_measurable"  # a declared metric with a collection path
    OBJECTIVE_ALERTED = "objective_alerted"  # an alert rule on that signal, with a delivery path
    UNSUPPORTED = "unsupported"  # no supported condition: never satisfied (results only)


@dataclass(frozen=True, slots=True)
class CheckResult(Check):
    """The verdict for one requirement or policy rule as an observability condition."""

    CONDITIONS = Condition
    ERROR = InvalidObservabilityResult


@dataclass(frozen=True, slots=True)
class ComponentResult:
    """One component's observability as the architecture declares it: its criticality, its coverage
    per dimension, its declared facts (with provenance) and what it does not declare."""

    node_id: str
    criticality: str | None  # as declared; None: not modeled
    coverage: Mapping[Dimension, CoverageState]
    inputs: tuple[Evidence, ...] = ()
    missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _sorted(self, "missing")
        coverage = self.coverage
        ok = (
            isinstance(coverage, Mapping)
            and set(coverage) == set(Dimension)
            and all(isinstance(v, CoverageState) for v in coverage.values())
        )
        if ok:
            object.__setattr__(self, "coverage", {d: coverage[d] for d in Dimension})
        _check(
            [
                None if isinstance(self.node_id, str) and 0 < len(self.node_id) <= MAX_ID else "node_id",
                None if self.criticality in (None, "critical", "standard") else "criticality",
                None if ok else "coverage",
                evidence_problem(self.inputs, "inputs"),
                ids_problem(self.missing, "missing"),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "criticality": self.criticality,
            "coverage": {d.value: s.value for d, s in self.coverage.items()},
            "inputs": [e.to_dict() for e in self.inputs],
            "missing": list(self.missing),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                data["node_id"],
                data.get("criticality"),
                {Dimension(d): CoverageState(s) for d, s in data["coverage"].items()},
                read_evidence(data.get("inputs")),
                tuple(data.get("missing") or ()),
            )
        except (KeyError, ValueError, TypeError, AttributeError) as error:
            raise InvalidObservabilityResult(details={"fields": [type(error).__name__]}) from None


def coverage_counts(components: tuple[ComponentResult, ...]) -> dict[str, dict[str, int]]:
    """Per dimension, how many components are in each coverage state (every state listed, zeros
    included): reproducible from the component results alone."""
    return {
        d.value: {s.value: sum(1 for c in components if c.coverage[d] is s) for s in CoverageState}
        for d in Dimension
    }


@dataclass(frozen=True, slots=True)
class ObservabilityResult:
    analyzer_set: ModelSet
    context_fingerprint: str  # identifies the inputs besides the IR (scope, policy, requirements, …)
    components: tuple[ComponentResult, ...] = ()
    findings: tuple[ObservabilityFinding, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", tuple(sorted(self.components, key=lambda c: c.node_id)))
        unique: dict[str, ObservabilityFinding] = {}  # one finding with the same identity: the first
        for f in sorted(self.findings, key=lambda f: (f.id, json.dumps(f.to_dict(), sort_keys=True))):
            unique.setdefault(f.id, f)
        object.__setattr__(
            self, "findings", tuple(sorted(unique.values(), key=ObservabilityFinding.sort_key))
        )
        object.__setattr__(self, "checks", tuple(sorted(self.checks, key=lambda c: c.key)))
        object.__setattr__(
            self, "unsupported", tuple(sorted(set(self.unsupported), key=lambda u: (u.element_id, u.code)))
        )
        object.__setattr__(self, "limitations", tuple(sorted(set(self.limitations), key=lambda x: x.code)))
        nodes = [c.node_id for c in self.components]
        keys = [c.key for c in self.checks]
        _check(
            [
                None if len(nodes) == len(set(nodes)) else "components",
                None if len(keys) == len(set(keys)) else "checks",
            ]
        )

    @property
    def status(self) -> ObservabilityStatus:
        if not self.components:
            return ObservabilityStatus.UNSUPPORTED
        states = [
            s for c in self.components for s in c.coverage.values() if s is not CoverageState.UNSUPPORTED
        ]
        if all(s is CoverageState.UNKNOWN for s in states):
            return ObservabilityStatus.INSUFFICIENT_INPUT
        if CoverageState.UNKNOWN not in states and not self.unsupported:
            return ObservabilityStatus.COMPLETED
        return ObservabilityStatus.PARTIAL

    def summary(self) -> dict[str, Any]:
        """Counts only — coverage states per dimension (all components in scope, and those declared
        critical), findings by severity, basis and category, checks by verdict. No score, no
        percentage: every count is reproducible from the components, findings and checks."""
        critical = tuple(c for c in self.components if c.criticality == "critical")
        severities = Counter(f.severity.value for f in self.findings)
        bases = Counter(f.basis.value for f in self.findings)
        categories = Counter(f.category.value for f in self.findings)
        verdicts = Counter(c.verdict.value for c in self.checks)
        criticality = Counter(c.criticality or "not_modeled" for c in self.components)
        return {
            "components": len(self.components),
            "criticality": {k: criticality.get(k, 0) for k in ("critical", "standard", "not_modeled")},
            "coverage": coverage_counts(self.components),
            "critical_coverage": coverage_counts(critical),
            "findings": {s.value: severities.get(s.value, 0) for s in Severity},
            "bases": {b.value: bases.get(b.value, 0) for b in FindingBasis},
            "categories": {c.value: categories.get(c.value, 0) for c in FindingCategory},
            "checks": {v.value: verdicts.get(v.value, 0) for v in Verdict},
            "unsupported": len(self.unsupported),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "analyzer_set": self.analyzer_set.to_dict(),
            "context_fingerprint": self.context_fingerprint,
            "status": self.status.value,
            "summary": self.summary(),
            "components": [c.to_dict() for c in self.components],
            "findings": [f.to_dict() for f in self.findings],
            "checks": [c.to_dict() for c in self.checks],
            "unsupported": [u.to_dict() for u in self.unsupported],
            "limitations": [x.to_dict() for x in self.limitations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ObservabilityResult:
        try:
            return cls(
                ModelSet.from_dict(data["analyzer_set"]),
                data["context_fingerprint"],
                tuple(ComponentResult.from_dict(c) for c in data.get("components") or ()),
                tuple(ObservabilityFinding.from_dict(f) for f in data.get("findings") or ()),
                tuple(CheckResult.from_dict(c) for c in data.get("checks") or ()),
                tuple(Unsupported.from_dict(u) for u in data.get("unsupported") or ()),
                tuple(Limitation.from_dict(x) for x in data.get("limitations") or ()),
            )
        except (KeyError, TypeError) as error:
            raise InvalidObservabilityResult(details={"fields": [type(error).__name__]}) from None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
