"""What a reliability analysis produces: estimates with their provenance, findings for human
review, objective verdicts, and results that never turn an unknown into 0 or 1.

- An **estimate** (capacity's ``Estimate``) is one value about one element: its ``availability``
  (a ``ratio`` quantity), ``recovery_time`` or ``data_loss_window`` (seconds), with its ``source``
  (``declared`` or ``model_estimate``), its ``basis`` (the formula or property), its inputs, and, when
  unknown, no value and what is missing.
- A **path** is a request path from an entry: the nodes and connections it requires, and its
  availability only when every element on it has one and the composition is supported.
- A **finding** is something worth a human look: what was detected, which elements, the evidence,
  why it matters, what is missing, and options to investigate. Its severity follows validation's
  scale; its certainty is ``modeled`` (the inputs establish it) or ``candidate`` (the evidence is
  incomplete). There is no risk score, and no finding claims an outage will happen.
- An **objective result** is validation's verdict for one reliability objective: ``satisfied`` or
  ``violated`` only by modeled evidence, else ``not_verifiable``; ``not_applicable`` when nothing
  it concerns exists.

Estimates are architecture-level estimates from declared inputs and stated models, never
guaranteed or measured uptime.
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
from core.domain.capacity.results import Certainty, ComponentStatus, Estimate
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

from .errors import InvalidReliabilityResult

FINDING_ID = re.compile(r"^rel_[0-9a-f]{16}$")
# Lists about elements are bounded by the largest architecture (1,000 nodes, 5,000 connections),
# not by a smaller fixed count: a path, a cycle or a finding can name every one of them.
MAX_PATH = MAX_NODES + MAX_CONNECTIONS
MAX_ELEMENTS = MAX_NODES + MAX_CONNECTIONS


def _check(problems: list[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidReliabilityResult(details={"fields": found})


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
    return None if ok else "evidence"


def _path_ids(values: object, name: str, *, empty: bool = True) -> str | None:
    if not isinstance(values, tuple) or not (empty or values) or len(values) > MAX_PATH:
        return name
    return None if all(isinstance(v, str) and 0 < len(v) <= MAX_ID for v in values) else name


class ReliabilityStatus(StrEnum):
    COMPLETED = "completed"  # every component and path in scope has its estimates
    PARTIAL = "partial"  # some are estimated, some lack inputs or are unsupported
    INSUFFICIENT_INPUT = "insufficient_input"  # models apply, but nothing could be estimated
    UNSUPPORTED = "unsupported"  # no model applies to anything in scope
    FAILED = "failed"  # the engine could not produce a result


class FindingType(StrEnum):
    SINGLE_POINT_OF_FAILURE = "single_point_of_failure"
    NO_REDUNDANCY = "no_redundancy"
    CRITICAL_DEPENDENCY_WITHOUT_ALTERNATIVE = "critical_dependency_without_alternative"
    REDUNDANCY_WITHOUT_FAILURE_DOMAIN_SEPARATION = "redundancy_without_failure_domain_separation"
    POTENTIAL_CORRELATED_FAILURE = "potential_correlated_failure"
    INCONSISTENT_REDUNDANCY = "inconsistent_redundancy"
    MISSING_FAILOVER = "missing_failover"
    MISSING_RECOVERY_DATA = "missing_recovery_data"
    RECOVERY_EXCEEDS_OBJECTIVE = "recovery_exceeds_objective"
    DATA_LOSS_EXCEEDS_OBJECTIVE = "data_loss_exceeds_objective"
    AVAILABILITY_BELOW_OBJECTIVE = "availability_below_objective"
    OBJECTIVE_NOT_EVALUABLE = "objective_not_evaluable"
    UNMODELED_DEPENDENCY = "unmodeled_dependency"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    AVAILABILITY_NOT_EVALUABLE = "availability_not_evaluable"
    UNVERIFIED_RELIABILITY_DATA = "unverified_reliability_data"
    REDUNDANCY_BELOW_OBJECTIVE = "redundancy_below_objective"


@dataclass(frozen=True, slots=True)
class ReliabilityFinding:
    type: FindingType
    severity: Severity
    certainty: Certainty
    title: str
    explanation: str  # what was detected and why it matters
    recommendation: str  # options for human review, never an automatic change
    node_ids: tuple[str, ...] = ()
    connection_ids: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    assumptions: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    model_id: str | None = None
    model_version: int | None = None
    objective: str | None = None  # the objective it is about, if any (part of its identity)

    def __post_init__(self) -> None:
        for name in ("node_ids", "connection_ids", "missing", "assumptions"):
            value = getattr(self, name)
            if isinstance(value, tuple):
                object.__setattr__(self, name, tuple(sorted(set(value))))
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
                None if self.node_ids or self.connection_ids else "node_ids",
                _evidence(self.evidence),
                _ids(self.assumptions, "assumptions"),
                _ids(self.missing, "missing"),
                None if (self.model_id is None) == (self.model_version is None) else "model_version",
                text_problem(self.objective, "objective", required=False),
            ]
        )

    @property
    def id(self) -> str:
        """Stable: the same type about the same elements (and objective) has the same id in every
        analysis."""
        parts: list[Any] = [self.type.value, list(self.node_ids), list(self.connection_ids)]
        key = json.dumps(parts + ([self.objective] if self.objective is not None else []))
        return "rel_" + hashlib.sha256(key.encode()).hexdigest()[:16]

    def sort_key(self) -> tuple[int, str, str]:
        return (list(Severity).index(self.severity), self.type.value, self.id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
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
            "model_id": self.model_id,
            "model_version": self.model_version,
            "objective": self.objective,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
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
                model_id=data.get("model_id"),
                model_version=data.get("model_version"),
                objective=data.get("objective"),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidReliabilityResult(details={"fields": [type(error).__name__]}) from None


class ObjectiveKind(StrEnum):
    AVAILABILITY = "availability"  # at least a fraction
    RECOVERY_TIME = "recovery_time"  # RTO: at most a duration
    DATA_LOSS = "data_loss"  # RPO: at most a duration
    REDUNDANCY = "redundancy"  # at least a number of replicas or members
    UNSUPPORTED = "unsupported"  # a requirement no reliability model checks (results only)


@dataclass(frozen=True, slots=True)
class ObjectiveResult:
    """The verdict for one objective, from modeled evidence only."""

    key: str
    kind: ObjectiveKind
    target: str  # the objective as stated, e.g. "0.999", "900 s", "2"
    verdict: Verdict
    explanation: str
    node_ids: tuple[str, ...] = ()  # what it was checked on
    actual: tuple[Evidence, ...] = ()  # the modeled values it was compared with
    missing: tuple[str, ...] = ()
    requirement_id: str | None = None  # the requirement it comes from, if any

    def __post_init__(self) -> None:
        for name in ("node_ids", "missing"):
            value = getattr(self, name)
            if isinstance(value, tuple):
                object.__setattr__(self, name, tuple(sorted(set(value))))
        _check(
            [
                text_problem(self.key, "key"),
                None if isinstance(self.kind, ObjectiveKind) else "kind",
                text_problem(self.target, "target"),
                None if isinstance(self.verdict, Verdict) else "verdict",
                text_problem(self.explanation, "explanation"),
                _ids(self.node_ids, "node_ids"),
                _evidence(self.actual),
                _ids(self.missing, "missing"),
                # missing evidence is never success
                None if self.verdict is not Verdict.SATISFIED or not self.missing else "verdict",
                text_problem(self.requirement_id, "requirement_id", required=False),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind.value,
            "target": self.target,
            "verdict": self.verdict.value,
            "explanation": self.explanation,
            "node_ids": list(self.node_ids),
            "actual": [e.to_dict() for e in self.actual],
            "missing": list(self.missing),
            "requirement_id": self.requirement_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                data["key"],
                ObjectiveKind(data["kind"]),
                data["target"],
                Verdict(data["verdict"]),
                data["explanation"],
                tuple(data.get("node_ids") or ()),
                read_evidence(data.get("actual")),
                tuple(data.get("missing") or ()),
                data.get("requirement_id"),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidReliabilityResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class PathResult:
    """A request path from an entry: the elements it requires in order, and its availability (an
    estimate whose ``element_id`` is the entry), known only when the whole path is."""

    entry_id: str
    node_ids: tuple[str, ...]
    connection_ids: tuple[str, ...]
    availability: Estimate
    optional_connection_ids: tuple[str, ...] = ()  # asynchronous or non-critical: not required

    def __post_init__(self) -> None:
        _check(
            [
                None if isinstance(self.entry_id, str) and 0 < len(self.entry_id) <= MAX_ID else "entry_id",
                _path_ids(self.node_ids, "node_ids", empty=False),
                _path_ids(self.connection_ids, "connection_ids"),
                _path_ids(self.optional_connection_ids, "optional_connection_ids"),
                None if isinstance(self.availability, Estimate) else "availability",
            ]
        )

    @property
    def complete(self) -> bool:
        return self.availability.known

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "node_ids": list(self.node_ids),
            "connection_ids": list(self.connection_ids),
            "optional_connection_ids": list(self.optional_connection_ids),
            "availability": self.availability.to_dict(),
            "complete": self.complete,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                data["entry_id"],
                tuple(data["node_ids"]),
                tuple(data["connection_ids"]),
                Estimate.from_dict(data["availability"]),
                tuple(data.get("optional_connection_ids") or ()),
            )
        except (KeyError, TypeError) as error:
            raise InvalidReliabilityResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class ComponentResult:
    """Everything the analysis established about one node."""

    node_id: str
    status: ComponentStatus
    models: tuple[tuple[str, int], ...] = ()
    estimates: tuple[Estimate, ...] = ()  # availability, recovery_time, data_loss_window
    inputs: tuple[Evidence, ...] = ()  # the reliability facts it declares
    missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "estimates", tuple(sorted(self.estimates, key=Estimate.sort_key)))
        if isinstance(self.missing, tuple):
            object.__setattr__(self, "missing", tuple(sorted(set(self.missing))))
        _check(
            [
                None if isinstance(self.node_id, str) and 0 < len(self.node_id) <= MAX_ID else "node_id",
                None if isinstance(self.status, ComponentStatus) else "status",
                None if all(isinstance(e, Estimate) for e in self.estimates) else "estimates",
                _evidence(self.inputs),
                _ids(self.missing, "missing"),
            ]
        )

    def estimate(self, resource: str) -> Estimate | None:
        return next((e for e in self.estimates if e.resource == resource), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": self.status.value,
            "models": [list(m) for m in self.models],
            "estimates": [e.to_dict() for e in self.estimates],
            "inputs": [e.to_dict() for e in self.inputs],
            "missing": list(self.missing),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                data["node_id"],
                ComponentStatus(data["status"]),
                tuple((m[0], m[1]) for m in data.get("models") or ()),
                tuple(Estimate.from_dict(e) for e in data.get("estimates") or ()),
                read_evidence(data.get("inputs")),
                tuple(data.get("missing") or ()),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidReliabilityResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class ReliabilityResult:
    model_set: ModelSet
    context_fingerprint: str  # identifies the inputs besides the IR (objectives, entries, assumptions)
    components: tuple[ComponentResult, ...] = ()
    paths: tuple[PathResult, ...] = ()
    findings: tuple[ReliabilityFinding, ...] = ()
    objectives: tuple[ObjectiveResult, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", tuple(sorted(self.components, key=lambda c: c.node_id)))
        object.__setattr__(self, "paths", tuple(sorted(self.paths, key=lambda p: (p.entry_id, p.node_ids))))
        unique: dict[str, ReliabilityFinding] = {}  # one finding of a type about the same elements: the first
        for f in sorted(self.findings, key=lambda f: (f.id, json.dumps(f.to_dict(), sort_keys=True))):
            unique.setdefault(f.id, f)  # in a fixed order, whatever the input order
        object.__setattr__(self, "findings", tuple(sorted(unique.values(), key=ReliabilityFinding.sort_key)))
        object.__setattr__(self, "objectives", tuple(sorted(self.objectives, key=lambda o: o.key)))
        object.__setattr__(
            self, "unsupported", tuple(sorted(set(self.unsupported), key=lambda u: (u.element_id, u.code)))
        )
        object.__setattr__(self, "limitations", tuple(sorted(set(self.limitations), key=lambda x: x.code)))
        ids = [c.node_id for c in self.components]
        _check([None if len(ids) == len(set(ids)) else "components"])

    @property
    def status(self) -> ReliabilityStatus:
        statuses = Counter(c.status for c in self.components)
        if not self.components:
            return ReliabilityStatus.UNSUPPORTED if self.unsupported else ReliabilityStatus.INSUFFICIENT_INPUT
        if statuses[ComponentStatus.ESTIMATED] == len(self.components) and all(
            p.complete for p in self.paths
        ):
            return ReliabilityStatus.COMPLETED if not self.unsupported else ReliabilityStatus.PARTIAL
        if statuses[ComponentStatus.ESTIMATED] or any(p.complete for p in self.paths):
            return ReliabilityStatus.PARTIAL
        if statuses[ComponentStatus.INSUFFICIENT_INPUT]:
            return ReliabilityStatus.INSUFFICIENT_INPUT
        return ReliabilityStatus.UNSUPPORTED

    def summary(self) -> dict[str, Any]:
        """Counts and coverage; no score."""
        severities = Counter(f.severity.value for f in self.findings)
        verdicts = Counter(o.verdict.value for o in self.objectives)
        components = Counter(c.status.value for c in self.components)
        return {
            "components": {s.value: components.get(s.value, 0) for s in ComponentStatus},
            "findings": {s.value: severities.get(s.value, 0) for s in Severity},
            "objectives": {v.value: verdicts.get(v.value, 0) for v in Verdict},
            "paths": len(self.paths),
            "paths_estimated": sum(p.complete for p in self.paths),
            "unsupported": len(self.unsupported),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_set": self.model_set.to_dict(),
            "context_fingerprint": self.context_fingerprint,
            "status": self.status.value,
            "summary": self.summary(),
            "components": [c.to_dict() for c in self.components],
            "paths": [p.to_dict() for p in self.paths],
            "findings": [f.to_dict() for f in self.findings],
            "objectives": [o.to_dict() for o in self.objectives],
            "unsupported": [u.to_dict() for u in self.unsupported],
            "limitations": [x.to_dict() for x in self.limitations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReliabilityResult:
        try:
            return cls(
                ModelSet.from_dict(data["model_set"]),
                data["context_fingerprint"],
                tuple(ComponentResult.from_dict(c) for c in data.get("components") or ()),
                tuple(PathResult.from_dict(p) for p in data.get("paths") or ()),
                tuple(ReliabilityFinding.from_dict(f) for f in data.get("findings") or ()),
                tuple(ObjectiveResult.from_dict(o) for o in data.get("objectives") or ()),
                tuple(Unsupported.from_dict(u) for u in data.get("unsupported") or ()),
                tuple(Limitation.from_dict(x) for x in data.get("limitations") or ()),
            )
        except (KeyError, TypeError) as error:
            raise InvalidReliabilityResult(details={"fields": [type(error).__name__]}) from None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
