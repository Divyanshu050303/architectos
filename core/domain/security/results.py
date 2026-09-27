"""What a security analysis produces: findings for human review, requirement and policy checks, the
trust zones and each component's modeled controls — never a score, never a claim that anything is
secure.

- A **finding** is something worth an engineer's look. Its **type** fixes its category (trust
  boundary, authentication, …) and its **basis**, kept apart on purpose:
  ``control_gap`` (the model states a control is absent or disabled where it matters),
  ``potential_risk`` (the modeled structure could allow harm; not established that it does),
  ``violation`` (an explicit requirement or policy is contradicted by modeled evidence) and
  ``not_evaluable`` (the model does not say enough to decide). Its severity follows validation's
  scale; its certainty is ``modeled`` (declared facts establish it) or ``candidate`` (the evidence is
  incomplete, inferred or proposed by a language model). Threat candidates carry a STRIDE category.
  Evidence never shows a secret's value.
- A **check** is validation's verdict for one requirement or policy rule: ``satisfied`` or
  ``violated`` only by modeled evidence, else ``not_verifiable`` (also when the requirement is not a
  supported condition); ``not_applicable`` when nothing it concerns exists.
- No finding means only that nothing was detected in what the architecture models: it does not
  prove the absence of vulnerabilities.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from core.architecture_ir.model import MAX_CONNECTIONS, MAX_NODES
from core.domain.capacity.results import Certainty
from core.domain.engine_results import (
    MAX_ID,
    Evidence,
    Limitation,
    ModelSet,
    Unsupported,
    read_evidence,
    text_problem,
)
from core.domain.validation.results import Severity, Verdict

from .errors import InvalidSecurityResult
from .values import shows_a_secret

FINDING_ID = re.compile(r"^sec_[0-9a-f]{16}$")
# Lists about elements are bounded by the largest architecture (1,000 nodes, 5,000 connections).
MAX_ELEMENTS = MAX_NODES + MAX_CONNECTIONS


def _check(problems: list[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidSecurityResult(details={"fields": found})


def _ids(values: object, name: str) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ELEMENTS:
        return name
    return None if all(isinstance(v, str) and 0 < len(v) <= MAX_ID for v in values) else name


def _evidence(values: object) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ELEMENTS:
        return "evidence"
    ok = all(
        isinstance(e, Evidence) and isinstance(e.label, str) and isinstance(e.value, str) for e in values
    )
    return None if ok and not shows_a_secret(values) else "evidence"  # never a secret's value


def _sorted(data: object, name: str) -> None:
    """Sort and deduplicate a tuple field in place (frozen dataclass)."""
    value = getattr(data, name)
    if isinstance(value, tuple):
        object.__setattr__(data, name, tuple(sorted(set(value))))


class SecurityStatus(StrEnum):
    COMPLETED = "completed"  # every analyzer ran and every component in scope models its controls
    PARTIAL = "partial"  # some controls are modeled, some are not, or an analyzer could not run
    INSUFFICIENT_INPUT = "insufficient_input"  # components exist, none models any security property
    UNSUPPORTED = "unsupported"  # nothing in scope to analyze
    FAILED = "failed"  # the engine could not produce a result


class FindingBasis(StrEnum):
    CONTROL_GAP = "control_gap"
    POTENTIAL_RISK = "potential_risk"
    VIOLATION = "violation"
    NOT_EVALUABLE = "not_evaluable"


class FindingCategory(StrEnum):
    TRUST_BOUNDARY = "trust_boundary"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    ENCRYPTION = "encryption"
    DATA_PROTECTION = "data_protection"
    SECRETS = "secrets"
    EXPOSURE = "exposure"
    THREAT = "threat"
    REQUIREMENT = "requirement"
    POLICY = "policy"


class StrideCategory(StrEnum):
    SPOOFING = "spoofing"
    TAMPERING = "tampering"
    REPUDIATION = "repudiation"
    INFORMATION_DISCLOSURE = "information_disclosure"
    DENIAL_OF_SERVICE = "denial_of_service"
    ELEVATION_OF_PRIVILEGE = "elevation_of_privilege"


class FindingType(StrEnum):
    # trust boundaries
    UNPROTECTED_BOUNDARY_CROSSING = "unprotected_boundary_crossing"
    CROSSING_CONTROLS_NOT_MODELED = "crossing_controls_not_modeled"
    SENSITIVE_DATA_CROSSES_BOUNDARY = "sensitive_data_crosses_boundary"
    TRUST_LEVEL_NOT_MODELED = "trust_level_not_modeled"
    INCONSISTENT_TRUST_BOUNDARY = "inconsistent_trust_boundary"
    INSUFFICIENT_FLOW_SEMANTICS = "insufficient_flow_semantics"
    # authentication and authorization
    MISSING_AUTHENTICATION = "missing_authentication"
    UNAUTHENTICATED_CONNECTION = "unauthenticated_connection"
    INCONSISTENT_AUTHENTICATION = "inconsistent_authentication"
    AUTHENTICATION_NOT_MODELED = "authentication_not_modeled"
    MISSING_AUTHORIZATION = "missing_authorization"
    AUTHORIZATION_NOT_MODELED = "authorization_not_modeled"
    # encryption and data protection
    UNENCRYPTED_DATA_AT_REST = "unencrypted_data_at_rest"
    UNENCRYPTED_DATA_IN_TRANSIT = "unencrypted_data_in_transit"
    ENCRYPTION_NOT_MODELED = "encryption_not_modeled"
    DATA_CLASSIFICATION_NOT_MODELED = "data_classification_not_modeled"
    # secrets
    HARDCODED_SECRET = "hardcoded_secret"  # noqa: S105 — a finding type, not a credential
    SECRET_IN_CONFIGURATION = "secret_in_configuration"  # noqa: S105 — a finding type, not a credential
    SECRET_SOURCE_NOT_MODELED = "secret_source_not_modeled"  # noqa: S105 — a finding type, not a credential
    # exposure
    PUBLIC_MANAGEMENT_INTERFACE = "public_management_interface"
    SENSITIVE_COMPONENT_REACHABLE_FROM_PUBLIC = "sensitive_component_reachable_from_public"
    EXPOSURE_NOT_MODELED = "exposure_not_modeled"
    # threats
    THREAT_CANDIDATE = "threat_candidate"
    # requirements and policy
    REQUIREMENT_VIOLATED = "requirement_violated"
    REQUIREMENT_NOT_EVALUABLE = "requirement_not_evaluable"
    POLICY_VIOLATED = "policy_violated"
    POLICY_NOT_EVALUABLE = "policy_not_evaluable"


_B, _C, _T = FindingBasis, FindingCategory, FindingType
# Each type's category and basis: fixed, so the four kinds of finding are never mixed up.
TYPES: dict[FindingType, tuple[FindingCategory, FindingBasis]] = {
    _T.UNPROTECTED_BOUNDARY_CROSSING: (_C.TRUST_BOUNDARY, _B.CONTROL_GAP),
    _T.CROSSING_CONTROLS_NOT_MODELED: (_C.TRUST_BOUNDARY, _B.NOT_EVALUABLE),
    _T.SENSITIVE_DATA_CROSSES_BOUNDARY: (_C.TRUST_BOUNDARY, _B.POTENTIAL_RISK),
    _T.TRUST_LEVEL_NOT_MODELED: (_C.TRUST_BOUNDARY, _B.NOT_EVALUABLE),
    _T.INCONSISTENT_TRUST_BOUNDARY: (_C.TRUST_BOUNDARY, _B.NOT_EVALUABLE),
    _T.INSUFFICIENT_FLOW_SEMANTICS: (_C.TRUST_BOUNDARY, _B.NOT_EVALUABLE),
    _T.MISSING_AUTHENTICATION: (_C.AUTHENTICATION, _B.CONTROL_GAP),
    _T.UNAUTHENTICATED_CONNECTION: (_C.AUTHENTICATION, _B.CONTROL_GAP),
    _T.INCONSISTENT_AUTHENTICATION: (_C.AUTHENTICATION, _B.POTENTIAL_RISK),
    _T.AUTHENTICATION_NOT_MODELED: (_C.AUTHENTICATION, _B.NOT_EVALUABLE),
    _T.MISSING_AUTHORIZATION: (_C.AUTHORIZATION, _B.CONTROL_GAP),
    _T.AUTHORIZATION_NOT_MODELED: (_C.AUTHORIZATION, _B.NOT_EVALUABLE),
    _T.UNENCRYPTED_DATA_AT_REST: (_C.ENCRYPTION, _B.CONTROL_GAP),
    _T.UNENCRYPTED_DATA_IN_TRANSIT: (_C.ENCRYPTION, _B.CONTROL_GAP),
    _T.ENCRYPTION_NOT_MODELED: (_C.ENCRYPTION, _B.NOT_EVALUABLE),
    _T.DATA_CLASSIFICATION_NOT_MODELED: (_C.DATA_PROTECTION, _B.NOT_EVALUABLE),
    _T.HARDCODED_SECRET: (_C.SECRETS, _B.CONTROL_GAP),
    _T.SECRET_IN_CONFIGURATION: (_C.SECRETS, _B.POTENTIAL_RISK),
    _T.SECRET_SOURCE_NOT_MODELED: (_C.SECRETS, _B.NOT_EVALUABLE),
    _T.PUBLIC_MANAGEMENT_INTERFACE: (_C.EXPOSURE, _B.CONTROL_GAP),
    _T.SENSITIVE_COMPONENT_REACHABLE_FROM_PUBLIC: (_C.EXPOSURE, _B.POTENTIAL_RISK),
    _T.EXPOSURE_NOT_MODELED: (_C.EXPOSURE, _B.NOT_EVALUABLE),
    _T.THREAT_CANDIDATE: (_C.THREAT, _B.POTENTIAL_RISK),
    _T.REQUIREMENT_VIOLATED: (_C.REQUIREMENT, _B.VIOLATION),
    _T.REQUIREMENT_NOT_EVALUABLE: (_C.REQUIREMENT, _B.NOT_EVALUABLE),
    _T.POLICY_VIOLATED: (_C.POLICY, _B.VIOLATION),
    _T.POLICY_NOT_EVALUABLE: (_C.POLICY, _B.NOT_EVALUABLE),
}
assert set(TYPES) == set(FindingType)  # noqa: S101 - every type has its category and basis


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    type: FindingType
    severity: Severity
    certainty: Certainty
    title: str
    explanation: str  # what was detected, why it matters, and what it does not establish
    recommendation: str  # what to investigate or change, for human review; never applied automatically
    node_ids: tuple[str, ...] = ()
    connection_ids: tuple[str, ...] = ()
    boundary_ids: tuple[str, ...] = ()  # the trust zones it concerns
    evidence: tuple[Evidence, ...] = ()
    assumptions: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()  # what the model would need to say to decide
    analyzer_id: str | None = None
    analyzer_version: int | None = None
    threat: StrideCategory | None = None  # threat candidates only (part of the identity)
    requirement_id: str | None = None  # the requirement it concerns (part of the identity)
    policy_rule: str | None = None  # the policy field it concerns (part of the identity)

    def __post_init__(self) -> None:
        for name in ("node_ids", "connection_ids", "boundary_ids", "missing", "assumptions"):
            _sorted(self, name)
        threat = self.type is FindingType.THREAT_CANDIDATE
        category = TYPES[self.type][0] if isinstance(self.type, FindingType) else None
        _check(
            [
                None if isinstance(self.type, FindingType) else "type",
                None if isinstance(self.severity, Severity) else "severity",
                None if isinstance(self.certainty, Certainty) else "certainty",
                text_problem(self.title, "title"),
                text_problem(self.explanation, "explanation"),
                text_problem(self.recommendation, "recommendation"),
                _ids(self.node_ids, "node_ids"),
                _ids(self.connection_ids, "connection_ids"),
                _ids(self.boundary_ids, "boundary_ids"),
                None if self.node_ids or self.connection_ids or self.boundary_ids else "node_ids",
                _evidence(self.evidence),
                _ids(self.assumptions, "assumptions"),
                _ids(self.missing, "missing"),
                None if (self.analyzer_id is None) == (self.analyzer_version is None) else "analyzer_version",
                None if isinstance(self.threat, StrideCategory) == threat else "threat",
                None
                if (self.requirement_id is not None) == (category is FindingCategory.REQUIREMENT)
                else "requirement_id",
                None
                if (self.policy_rule is not None) == (category is FindingCategory.POLICY)
                else "policy_rule",
                text_problem(self.requirement_id, "requirement_id", required=False),
                text_problem(self.policy_rule, "policy_rule", required=False),
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
        """Stable: the same type about the same elements (and threat, requirement or policy rule)
        has the same id in every analysis."""
        parts: list[Any] = [
            self.type.value,
            list(self.node_ids),
            list(self.connection_ids),
            list(self.boundary_ids),
        ]
        for extra in (self.threat, self.requirement_id, self.policy_rule):
            if extra is not None:
                parts.append(str(extra))
        return "sec_" + hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]

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
            "boundary_ids": list(self.boundary_ids),
            "evidence": [e.to_dict() for e in self.evidence],
            "assumptions": list(self.assumptions),
            "missing": list(self.missing),
            "analyzer_id": self.analyzer_id,
            "analyzer_version": self.analyzer_version,
            "threat": self.threat.value if self.threat is not None else None,
            "requirement_id": self.requirement_id,
            "policy_rule": self.policy_rule,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            threat = data.get("threat")
            return cls(
                type=FindingType(data["type"]),
                severity=Severity(data["severity"]),
                certainty=Certainty(data["certainty"]),
                title=data["title"],
                explanation=data["explanation"],
                recommendation=data["recommendation"],
                node_ids=tuple(data.get("node_ids") or ()),
                connection_ids=tuple(data.get("connection_ids") or ()),
                boundary_ids=tuple(data.get("boundary_ids") or ()),
                evidence=read_evidence(data.get("evidence")),
                assumptions=tuple(data.get("assumptions") or ()),
                missing=tuple(data.get("missing") or ()),
                analyzer_id=data.get("analyzer_id"),
                analyzer_version=data.get("analyzer_version"),
                threat=StrideCategory(threat) if threat is not None else None,
                requirement_id=data.get("requirement_id"),
                policy_rule=data.get("policy_rule"),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidSecurityResult(details={"fields": [type(error).__name__]}) from None


class Condition(StrEnum):
    """What a requirement or policy rule is checked as: a fixed, machine-checkable condition."""

    ENCRYPTION_IN_TRANSIT = "encryption_in_transit"
    ENCRYPTION_AT_REST = "encryption_at_rest"
    AUTHENTICATION_ON_PUBLIC = "authentication_on_public"
    AUTHORIZATION_ON_SENSITIVE = "authorization_on_sensitive"
    NO_PUBLIC_MANAGEMENT_INTERFACE = "no_public_management_interface"
    APPROVED_SECRET_SOURCE = "approved_secret_source"  # noqa: S105 — a condition, not a credential
    SECRET_ROTATION = "secret_rotation"  # noqa: S105 — a condition, not a credential
    AUDIT_LOGGING = "audit_logging"
    DATA_CLASSIFICATION = "data_classification"
    UNSUPPORTED = "unsupported"  # no supported condition: never satisfied (results only)


class CheckSource(StrEnum):
    REQUIREMENT = "requirement"
    POLICY = "policy"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The verdict for one requirement or policy rule, from modeled evidence only."""

    key: str  # "requirement.<id>" or "policy.<field>"
    source: CheckSource
    condition: Condition
    verdict: Verdict
    explanation: str
    node_ids: tuple[str, ...] = ()  # what it was checked on
    connection_ids: tuple[str, ...] = ()
    actual: tuple[Evidence, ...] = ()  # the modeled values it was judged on
    missing: tuple[str, ...] = ()
    requirement_id: str | None = None
    policy_rule: str | None = None
    mapping: str | None = (
        None  # how a requirement's words became the condition, e.g. "encryption + 'at rest'"
    )

    def __post_init__(self) -> None:
        for name in ("node_ids", "connection_ids", "missing"):
            _sorted(self, name)
        requirement = self.source is CheckSource.REQUIREMENT
        _check(
            [
                text_problem(self.key, "key"),
                None if isinstance(self.source, CheckSource) else "source",
                None if isinstance(self.condition, Condition) else "condition",
                None if isinstance(self.verdict, Verdict) else "verdict",
                text_problem(self.explanation, "explanation"),
                _ids(self.node_ids, "node_ids"),
                _ids(self.connection_ids, "connection_ids"),
                _evidence(self.actual),
                _ids(self.missing, "missing"),
                # missing evidence, or an unsupported condition, is never success
                None
                if self.verdict is not Verdict.SATISFIED
                or not (self.missing or self.condition is Condition.UNSUPPORTED)
                else "verdict",
                None
                if self.condition is not Condition.UNSUPPORTED or self.verdict is Verdict.NOT_VERIFIABLE
                else "verdict",
                None if (self.requirement_id is not None) == requirement else "requirement_id",
                None if (self.policy_rule is not None) == (not requirement) else "policy_rule",
                text_problem(self.requirement_id, "requirement_id", required=False),
                text_problem(self.policy_rule, "policy_rule", required=False),
                text_problem(self.mapping, "mapping", required=False),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "source": self.source.value,
            "condition": self.condition.value,
            "verdict": self.verdict.value,
            "explanation": self.explanation,
            "node_ids": list(self.node_ids),
            "connection_ids": list(self.connection_ids),
            "actual": [e.to_dict() for e in self.actual],
            "missing": list(self.missing),
            "requirement_id": self.requirement_id,
            "policy_rule": self.policy_rule,
            "mapping": self.mapping,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                data["key"],
                CheckSource(data["source"]),
                Condition(data["condition"]),
                Verdict(data["verdict"]),
                data["explanation"],
                tuple(data.get("node_ids") or ()),
                tuple(data.get("connection_ids") or ()),
                read_evidence(data.get("actual")),
                tuple(data.get("missing") or ()),
                data.get("requirement_id"),
                data.get("policy_rule"),
                data.get("mapping"),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidSecurityResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class TrustZone:
    """A boundary declared as a trust zone, its trust level (None: not modeled) and the components
    inside it, at any depth."""

    boundary_id: str
    trust_level: str | None
    node_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _sorted(self, "node_ids")
        _check(
            [
                None
                if isinstance(self.boundary_id, str) and 0 < len(self.boundary_id) <= MAX_ID
                else "boundary_id",
                text_problem(self.trust_level, "trust_level", required=False),
                _ids(self.node_ids, "node_ids"),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "trust_level": self.trust_level,
            "node_ids": list(self.node_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(data["boundary_id"], data.get("trust_level"), tuple(data.get("node_ids") or ()))
        except (KeyError, TypeError) as error:
            raise InvalidSecurityResult(details={"fields": [type(error).__name__]}) from None


class Coverage(StrEnum):
    MODELED = "modeled"  # every security property that matters for it is known
    PARTIAL = "partial"  # some are known, some are not
    NOT_MODELED = "not_modeled"  # none is


@dataclass(frozen=True, slots=True)
class ComponentResult:
    """What the architecture models about one component's security: its declared facts (with
    provenance), the properties that matter for it but are not modeled, and its trust zones."""

    node_id: str
    inputs: tuple[Evidence, ...] = ()
    missing: tuple[str, ...] = ()
    trust_zone_ids: tuple[str, ...] = ()
    exposure: str | None = None  # as declared; None: not modeled
    sensitive: bool | None = None  # by declared classification and personal data; None: not established

    def __post_init__(self) -> None:
        for name in ("missing", "trust_zone_ids"):
            _sorted(self, name)
        _check(
            [
                None if isinstance(self.node_id, str) and 0 < len(self.node_id) <= MAX_ID else "node_id",
                _evidence(self.inputs),
                _ids(self.missing, "missing"),
                _ids(self.trust_zone_ids, "trust_zone_ids"),
                text_problem(self.exposure, "exposure", required=False),
                None if self.sensitive is None or isinstance(self.sensitive, bool) else "sensitive",
            ]
        )

    @property
    def coverage(self) -> Coverage:
        if not self.missing:
            return Coverage.MODELED
        return Coverage.PARTIAL if self.inputs else Coverage.NOT_MODELED

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "coverage": self.coverage.value,
            "exposure": self.exposure,
            "sensitive": self.sensitive,
            "trust_zone_ids": list(self.trust_zone_ids),
            "inputs": [e.to_dict() for e in self.inputs],
            "missing": list(self.missing),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                data["node_id"],
                read_evidence(data.get("inputs")),
                tuple(data.get("missing") or ()),
                tuple(data.get("trust_zone_ids") or ()),
                data.get("exposure"),
                data.get("sensitive"),
            )
        except (KeyError, TypeError) as error:
            raise InvalidSecurityResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class SecurityResult:
    analyzer_set: ModelSet
    context_fingerprint: str  # identifies the inputs besides the IR (scope, policy, requirements, …)
    components: tuple[ComponentResult, ...] = ()
    trust_zones: tuple[TrustZone, ...] = ()
    findings: tuple[SecurityFinding, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", tuple(sorted(self.components, key=lambda c: c.node_id)))
        object.__setattr__(self, "trust_zones", tuple(sorted(self.trust_zones, key=lambda z: z.boundary_id)))
        unique: dict[str, SecurityFinding] = {}  # one finding with the same identity: the first
        for f in sorted(self.findings, key=lambda f: (f.id, json.dumps(f.to_dict(), sort_keys=True))):
            unique.setdefault(f.id, f)  # in a fixed order, whatever the input order
        object.__setattr__(self, "findings", tuple(sorted(unique.values(), key=SecurityFinding.sort_key)))
        object.__setattr__(self, "checks", tuple(sorted(self.checks, key=lambda c: c.key)))
        object.__setattr__(
            self, "unsupported", tuple(sorted(set(self.unsupported), key=lambda u: (u.element_id, u.code)))
        )
        object.__setattr__(self, "limitations", tuple(sorted(set(self.limitations), key=lambda x: x.code)))
        nodes = [c.node_id for c in self.components]
        keys = [c.key for c in self.checks]
        zones = [z.boundary_id for z in self.trust_zones]
        _check(
            [
                None if len(nodes) == len(set(nodes)) else "components",
                None if len(keys) == len(set(keys)) else "checks",
                None if len(zones) == len(set(zones)) else "trust_zones",
            ]
        )

    @property
    def status(self) -> SecurityStatus:
        if not self.components:
            return SecurityStatus.UNSUPPORTED
        coverage = Counter(c.coverage for c in self.components)
        if coverage[Coverage.NOT_MODELED] == len(self.components):
            return SecurityStatus.INSUFFICIENT_INPUT
        if coverage[Coverage.MODELED] == len(self.components) and not self.unsupported:
            return SecurityStatus.COMPLETED
        return SecurityStatus.PARTIAL

    def summary(self) -> dict[str, Any]:
        """Counts and coverage; no score."""
        severities = Counter(f.severity.value for f in self.findings)
        bases = Counter(f.basis.value for f in self.findings)
        categories = Counter(f.category.value for f in self.findings)
        threats = Counter(f.threat.value for f in self.findings if f.threat is not None)
        verdicts = Counter(c.verdict.value for c in self.checks)
        coverage = Counter(c.coverage.value for c in self.components)
        return {
            "components": {c.value: coverage.get(c.value, 0) for c in Coverage},
            "findings": {s.value: severities.get(s.value, 0) for s in Severity},
            "bases": {b.value: bases.get(b.value, 0) for b in FindingBasis},
            "categories": {c.value: categories.get(c.value, 0) for c in FindingCategory},
            "threats": {t.value: threats.get(t.value, 0) for t in StrideCategory},
            "checks": {v.value: verdicts.get(v.value, 0) for v in Verdict},
            "trust_zones": len(self.trust_zones),
            "unsupported": len(self.unsupported),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "analyzer_set": self.analyzer_set.to_dict(),
            "context_fingerprint": self.context_fingerprint,
            "status": self.status.value,
            "summary": self.summary(),
            "components": [c.to_dict() for c in self.components],
            "trust_zones": [z.to_dict() for z in self.trust_zones],
            "findings": [f.to_dict() for f in self.findings],
            "checks": [c.to_dict() for c in self.checks],
            "unsupported": [u.to_dict() for u in self.unsupported],
            "limitations": [x.to_dict() for x in self.limitations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SecurityResult:
        try:
            return cls(
                ModelSet.from_dict(data["analyzer_set"]),
                data["context_fingerprint"],
                tuple(ComponentResult.from_dict(c) for c in data.get("components") or ()),
                tuple(TrustZone.from_dict(z) for z in data.get("trust_zones") or ()),
                tuple(SecurityFinding.from_dict(f) for f in data.get("findings") or ()),
                tuple(CheckResult.from_dict(c) for c in data.get("checks") or ()),
                tuple(Unsupported.from_dict(u) for u in data.get("unsupported") or ()),
                tuple(Limitation.from_dict(x) for x in data.get("limitations") or ()),
            )
        except (KeyError, TypeError) as error:
            raise InvalidSecurityResult(details={"fields": [type(error).__name__]}) from None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
