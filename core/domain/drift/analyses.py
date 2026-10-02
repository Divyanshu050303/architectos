"""A drift analysis: one comparison between an exact baseline revision and one discovery run.

**The request** names them — an architecture, the exact revision number, a discovery run of the same
project — with the comparison policy, optional exclusions and a label. It never carries topology or
findings: both sides are read from what is stored.

**The result** cites both sides exactly (the revision's content hash and schema version; the run's
result fingerprint, input fingerprint and the versions of its extractors and rules), states first
whether they can be compared (``compatibility``, per dimension) and what was inspected
(``coverage``), then lists the findings — in canonical order, with stable ids. "No difference" is
said only of the comparable, inspected scope (``no_difference_within_coverage``), never of the whole
system. There is no drift score, and no claim that an architecture is safe, compliant or ready.

**An analysis** moves ``pending`` → ``running`` → ``completed`` / ``completed_with_warnings`` /
``incompatible_inputs`` (the inputs cannot be compared: no findings), or ``failed`` / ``cancelled``.
"""

import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from core.domain.discovery.values import FINGERPRINT, KEY, MAX_REFERENCE

from .errors import InvalidDriftRequest, InvalidDriftTransition
from .findings import CompatibilityCheck, DriftFinding
from .values import (
    FINISHED,
    AnalysisStatus,
    Classification,
    Compatibility,
    check,
    code,
    count,
    fingerprint,
    items,
    texts,
    worst,
)

RESULT_VERSION = 1
MAX_FINDINGS = 10_000
MAX_EXCLUDED = 500
MAX_LABEL = 200
POLICIES = frozenset({"default"})  # the comparison policies the engine knows

S = AnalysisStatus
TRANSITIONS: dict[AnalysisStatus, frozenset[AnalysisStatus]] = {
    S.PENDING: frozenset({S.RUNNING, S.FAILED, S.CANCELLED}),
    S.RUNNING: frozenset(
        {S.COMPLETED, S.COMPLETED_WITH_WARNINGS, S.INCOMPATIBLE_INPUTS, S.FAILED, S.CANCELLED}
    ),
    **{finished: frozenset() for finished in FINISHED},
}


def _invalid(field_name: str, reason: str) -> InvalidDriftRequest:
    return InvalidDriftRequest(details={"field": field_name, "reason": reason})


def _identifier(value: object) -> bool:
    return isinstance(value, str) and len(value) <= MAX_REFERENCE and KEY.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class DriftRequest:
    architecture_id: uuid.UUID
    baseline_revision: int
    discovery_run_id: uuid.UUID
    policy: str = "default"
    exclude: tuple[str, ...] = ()  # baseline element ids or discovery keys left out of the comparison
    label: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.architecture_id, uuid.UUID):
            raise _invalid("architecture_id", "required")
        revision = self.baseline_revision
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise _invalid("baseline_revision", "invalid_revision")
        if not isinstance(self.discovery_run_id, uuid.UUID):
            raise _invalid("discovery_run_id", "required")
        if self.policy not in POLICIES:
            raise _invalid("policy", "unknown_policy")
        if not isinstance(self.exclude, tuple) or len(self.exclude) > MAX_EXCLUDED:
            raise _invalid("exclude", "too_many")
        if not all(_identifier(e) for e in self.exclude):
            raise _invalid("exclude", "invalid_identifier")
        label = self.label
        if label is not None and not (isinstance(label, str) and label.strip() and len(label) <= MAX_LABEL):
            raise _invalid("label", "invalid_text")
        object.__setattr__(self, "exclude", tuple(sorted(set(self.exclude))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture_id": str(self.architecture_id),
            "baseline_revision": self.baseline_revision,
            "discovery_run_id": str(self.discovery_run_id),
            "policy": self.policy,
            "exclude": list(self.exclude),
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class BaselineRef:
    """The exact, immutable revision compared."""

    architecture_id: uuid.UUID
    revision_number: int
    content_hash: str
    schema_version: int

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.architecture_id, uuid.UUID) else "baseline.architecture_id",
                count(self.revision_number, "baseline.revision_number", minimum=1),
                None if FINGERPRINT.fullmatch(str(self.content_hash)) else "baseline.content_hash",
                count(self.schema_version, "baseline.schema_version", minimum=1),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture_id": str(self.architecture_id),
            "revision_number": self.revision_number,
            "content_hash": self.content_hash,
            "schema_version": self.schema_version,
        }


def _versions(values: object) -> bool:
    return isinstance(values, Mapping) and all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) for k, v in values.items()
    )


@dataclass(frozen=True, slots=True)
class ObservedRef:
    """The exact discovery run compared: its result and inputs by fingerprint, and the versions that
    produced it."""

    discovery_run_id: uuid.UUID
    result_fingerprint: str
    sources_fingerprint: str
    result_version: int
    extractors: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.discovery_run_id, uuid.UUID) else "observed.discovery_run_id",
                None
                if FINGERPRINT.fullmatch(str(self.result_fingerprint))
                else "observed.result_fingerprint",
                None
                if FINGERPRINT.fullmatch(str(self.sources_fingerprint))
                else "observed.sources_fingerprint",
                count(self.result_version, "observed.result_version", minimum=1),
                None if _versions(self.extractors) else "observed.extractors",
            ]
        )
        object.__setattr__(self, "extractors", dict(sorted(self.extractors.items())))

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovery_run_id": str(self.discovery_run_id),
            "result_fingerprint": self.result_fingerprint,
            "sources_fingerprint": self.sources_fingerprint,
            "result_version": self.result_version,
            "extractors": dict(self.extractors),
        }


@dataclass(frozen=True, slots=True)
class Coverage:
    """What the discovery run inspected — the scope every conclusion is limited to."""

    source_types: tuple[str, ...] = ()
    inspected: tuple[str, ...] = ()  # artifact paths read completely
    partial: tuple[str, ...] = ()  # read only partly
    unread: tuple[str, ...] = ()  # unsupported or failed
    unsupported_constructs: int = 0
    unresolved_entities: int = 0
    unresolved_relationships: int = 0
    errors: int = 0

    def __post_init__(self) -> None:
        check(
            [
                texts(self.source_types, "coverage.source_types", 64),
                texts(self.inspected, "coverage.inspected", 256),
                texts(self.partial, "coverage.partial", 256),
                texts(self.unread, "coverage.unread", 256),
                count(self.unsupported_constructs, "coverage.unsupported_constructs"),
                count(self.unresolved_entities, "coverage.unresolved_entities"),
                count(self.unresolved_relationships, "coverage.unresolved_relationships"),
                count(self.errors, "coverage.errors"),
            ]
        )
        for name in ("source_types", "inspected", "partial", "unread"):
            object.__setattr__(self, name, tuple(sorted(set(getattr(self, name)))))

    @property
    def complete(self) -> bool:
        """Every supplied artifact read completely, with nothing unsupported or unresolved."""
        return not (
            self.partial
            or self.unread
            or self.unsupported_constructs
            or self.unresolved_entities
            or self.unresolved_relationships
            or self.errors
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_types": list(self.source_types),
            "inspected": list(self.inspected),
            "partial": list(self.partial),
            "unread": list(self.unread),
            "unsupported_constructs": self.unsupported_constructs,
            "unresolved_entities": self.unresolved_entities,
            "unresolved_relationships": self.unresolved_relationships,
            "errors": self.errors,
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class DriftResult:
    baseline: BaselineRef
    observed: ObservedRef
    compatibility: tuple[CompatibilityCheck, ...]
    coverage: Coverage
    findings: tuple[DriftFinding, ...] = ()
    warnings: tuple[str, ...] = ()
    versions: Mapping[str, int] = field(default_factory=dict)  # comparison engine, rules and policy
    policy: str = "default"
    version: int = RESULT_VERSION

    def __post_init__(self) -> None:
        ids = [f.id for f in self.findings]
        compared = [f for f in self.findings if f.classification is not Classification.NOT_COMPARABLE]
        check(
            [
                None if isinstance(self.baseline, BaselineRef) else "baseline",
                None if isinstance(self.observed, ObservedRef) else "observed",
                items(self.compatibility, CompatibilityCheck, "compatibility"),
                None if self.compatibility else "compatibility",
                None if isinstance(self.coverage, Coverage) else "coverage",
                items(self.findings, DriftFinding, "findings", MAX_FINDINGS),
                "findings" if len(ids) != len(set(ids)) else None,  # one finding per difference
                texts(self.warnings, "warnings"),
                None if _versions(self.versions) else "versions",
                code(self.policy, "policy"),
                None if self.version == RESULT_VERSION else "version",
                # Nothing is compared across incompatible inputs.
                "findings"
                if self.compatibility and self.status is Compatibility.INCOMPATIBLE and compared
                else None,
            ]
        )
        ordered = tuple(sorted(self.compatibility, key=lambda c: c.dimension))
        object.__setattr__(self, "compatibility", ordered)
        object.__setattr__(self, "findings", tuple(sorted(self.findings, key=lambda f: f.sort_key)))
        object.__setattr__(self, "warnings", tuple(sorted(set(self.warnings))))
        object.__setattr__(self, "versions", dict(sorted(self.versions.items())))

    @property
    def status(self) -> Compatibility:
        """The overall compatibility: the least comparable dimension."""
        return worst(c.outcome for c in self.compatibility)

    @property
    def no_difference_within_coverage(self) -> bool:
        """Comparable, and no difference found in the inspected scope — a statement about that scope,
        never about the whole system."""
        return self.status is not Compatibility.INCOMPATIBLE and not self.findings

    @property
    def has_warnings(self) -> bool:
        return bool(
            self.warnings
            or self.status is not Compatibility.COMPATIBLE
            or not self.coverage.complete
            or any(f.classification is not Classification.CONFIRMED for f in self.findings)
        )

    def summary(self) -> dict[str, Any]:
        """Counts only — no score."""
        return {
            "compatibility": self.status.value,
            "findings": len(self.findings),
            "types": dict(sorted(Counter(f.type.value for f in self.findings).items())),
            "classifications": dict(sorted(Counter(f.classification.value for f in self.findings).items())),
            "no_difference_within_coverage": self.no_difference_within_coverage,
        }

    def _content(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "policy": self.policy,
            "baseline": self.baseline.to_dict(),
            "observed": self.observed.to_dict(),
            "compatibility": [c.to_dict() for c in self.compatibility],
            "coverage": self.coverage.to_dict(),
            "findings": [f.to_dict() for f in self.findings],
            "warnings": list(self.warnings),
            "versions": dict(self.versions),
        }

    @property
    def fingerprint(self) -> str:
        return fingerprint(self._content())

    def to_dict(self) -> dict[str, Any]:
        return self._content() | {"summary": self.summary(), "fingerprint": self.fingerprint}


@dataclass(frozen=True, slots=True)
class AnalysisError:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class DriftAnalysis:
    id: uuid.UUID
    project_id: uuid.UUID
    request: DriftRequest
    status: AnalysisStatus
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: DriftResult | None = None
    error: AnalysisError | None = None

    def _move(self, to: AnalysisStatus) -> None:
        if to not in TRANSITIONS[self.status]:
            raise InvalidDriftTransition(details={"from": self.status.value, "to": to.value})

    def start(self, at: datetime) -> DriftAnalysis:
        self._move(S.RUNNING)
        return replace(self, status=S.RUNNING, started_at=at)

    def finish(self, result: DriftResult, at: datetime) -> DriftAnalysis:
        """Incompatible inputs, completed with warnings (limited coverage, anything not confirmed), or
        completed."""
        if result.status is Compatibility.INCOMPATIBLE:
            to = S.INCOMPATIBLE_INPUTS
        else:
            to = S.COMPLETED_WITH_WARNINGS if result.has_warnings else S.COMPLETED
        self._move(to)
        return replace(self, status=to, result=result, completed_at=at)

    def fail(self, error: AnalysisError, at: datetime) -> DriftAnalysis:
        self._move(S.FAILED)
        return replace(self, status=S.FAILED, error=error, completed_at=at)

    def cancel(self, at: datetime) -> DriftAnalysis:
        self._move(S.CANCELLED)
        return replace(self, status=S.CANCELLED, completed_at=at)
