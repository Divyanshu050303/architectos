"""A reliability analysis: one execution of the reliability models against one architecture
revision.

The **request** names the exact revision, where requests enter (``entries``: node ids; default the
clients), the **objectives** to check (availability at least a fraction, recovery time and data loss
at most a duration, redundancy at least a count; each on named components or the whole
architecture), and analysis-level assumptions. Component reliability inputs are not in the request:
they are properties of the architecture itself. Nothing is filled in by default.

Lifecycle: ``pending`` → ``running`` → a final status (what the result established, or ``failed``).
Final statuses are final. Analyses reference the revision (number and content hash); they never copy
it.
"""

import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from core.domain.capacity.units import Quantity
from core.domain.requirements.value_objects import decimal_to_str

from .errors import InvalidReliabilityAnalysisTransition, InvalidReliabilityRequest
from .results import ObjectiveKind, ReliabilityResult, ReliabilityStatus
from .values import fraction, in_seconds

KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
MAX_OBJECTIVES = 50
MAX_ASSUMPTIONS = 50
MAX_ENTRIES = 50
MAX_SCOPE = 200
MAX_ELEMENT_ID = 128
MAX_LABEL_LENGTH = 100
PENDING, RUNNING = "pending", "running"


def _invalid(field: str, reason: str) -> InvalidReliabilityRequest:
    return InvalidReliabilityRequest(details={"field": field, "reason": reason})


def _ids(values: object, field: str, limit: int) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > limit:
        raise _invalid(field, "too_many")
    if not all(isinstance(v, str) and 0 < len(v) <= MAX_ELEMENT_ID for v in values):
        raise _invalid(field, "invalid_reference")
    return tuple(sorted(set(values)))


@dataclass(frozen=True, slots=True)
class ReliabilityAssumption:
    """Something the analysis takes as true, stated by a person; recorded, never computed with."""

    key: str
    statement: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not KEY.fullmatch(self.key):
            raise _invalid("assumptions.key", "invalid_key")
        if not isinstance(self.statement, str) or not self.statement.strip() or len(self.statement) > 500:
            raise _invalid("assumptions.statement", "invalid_text")

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "statement": self.statement}


@dataclass(frozen=True, slots=True)
class Objective:
    """A machine-checkable reliability objective.

    - ``availability``: at least ``target`` (a fraction, e.g. 0.999), on each path from the entries,
      or on the named components;
    - ``recovery_time`` (RTO): at most ``duration``, for each named component (default: every one on
      a request path);
    - ``data_loss`` (RPO): at most ``duration``, for each named data store (default: every one);
    - ``redundancy``: at least ``target`` replicas (or group members) able to serve, per component.
    """

    key: str
    kind: ObjectiveKind
    target: Decimal | None = None  # availability (fraction) or redundancy (count)
    duration: Quantity | None = None  # recovery_time, data_loss
    node_ids: tuple[str, ...] = ()  # empty: the architecture (paths, or every relevant component)
    requirement_id: uuid.UUID | None = None  # the requirement it translates, if any

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not KEY.fullmatch(self.key):
            raise _invalid("objectives.key", "invalid_key")
        if not isinstance(self.kind, ObjectiveKind):
            raise _invalid("objectives.kind", "unknown_kind")
        object.__setattr__(self, "node_ids", _ids(self.node_ids, "objectives.node_ids", MAX_SCOPE))
        timed = self.kind in {ObjectiveKind.RECOVERY_TIME, ObjectiveKind.DATA_LOSS}
        if timed:
            if not isinstance(self.duration, Quantity) or self.target is not None:
                raise _invalid("objectives.duration", "required")
            in_seconds(self.duration, "objectives.duration")
        elif self.duration is not None:
            raise _invalid("objectives.duration", "not_applicable")
        elif self.kind is ObjectiveKind.AVAILABILITY:
            object.__setattr__(self, "target", fraction(self.target, "objectives.target"))
        else:
            count = self.target
            if (
                isinstance(count, bool)
                or not isinstance(count, int | Decimal)
                or count != int(count)
                or count < 1
            ):
                raise _invalid("objectives.target", "not_a_positive_count")
            object.__setattr__(self, "target", Decimal(int(count)))
        if self.requirement_id is not None and not isinstance(self.requirement_id, uuid.UUID):
            raise _invalid("objectives.requirement_id", "invalid_reference")

    @property
    def seconds(self) -> Decimal | None:
        return in_seconds(self.duration, "objectives.duration") if self.duration is not None else None

    @property
    def stated(self) -> str:
        if self.duration is not None:
            return f"{decimal_to_str(self.duration.value)} {self.duration.unit}"
        return decimal_to_str(self.target) if self.target is not None else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind.value,
            "target": decimal_to_str(self.target) if self.target is not None else None,
            "duration": self.duration.to_dict() if self.duration is not None else None,
            "node_ids": list(self.node_ids),
            "requirement_id": str(self.requirement_id) if self.requirement_id else None,
        }


@dataclass(frozen=True, slots=True)
class ReliabilityAnalysisRequest:
    architecture_id: uuid.UUID
    revision_number: int
    entries: tuple[str, ...] | None = None  # where requests enter; None: the clients
    objectives: tuple[Objective, ...] = ()
    assumptions: tuple[ReliabilityAssumption, ...] = ()
    label: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.architecture_id, uuid.UUID):
            raise _invalid("architecture_id", "invalid_reference")
        if (
            isinstance(self.revision_number, bool)
            or not isinstance(self.revision_number, int)
            or self.revision_number < 1
        ):
            raise _invalid("revision_number", "not_a_positive_count")
        if self.entries is not None:
            entries = _ids(self.entries, "entries", MAX_ENTRIES)
            if not entries:
                raise _invalid("entries", "empty")
            object.__setattr__(self, "entries", entries)
        for name, limit in (("objectives", MAX_OBJECTIVES), ("assumptions", MAX_ASSUMPTIONS)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or len(values) > limit:
                raise _invalid(name, "too_many")
            keys = [v.key for v in values]
            if len(keys) != len(set(keys)):
                raise _invalid(name, "duplicate_key")
            object.__setattr__(self, name, tuple(sorted(values, key=lambda v: v.key)))
        if not all(isinstance(o, Objective) for o in self.objectives):
            raise _invalid("objectives", "invalid_objective")
        if not all(isinstance(a, ReliabilityAssumption) for a in self.assumptions):
            raise _invalid("assumptions", "invalid_assumption")
        if self.label is not None and (
            not isinstance(self.label, str) or not self.label.strip() or len(self.label) > MAX_LABEL_LENGTH
        ):
            raise _invalid("label", "invalid_text")

    def inputs(self) -> dict[str, Any]:
        """Everything the result depends on besides the revision content, canonically."""
        return {
            "architecture_id": str(self.architecture_id),
            "revision_number": self.revision_number,
            "entries": list(self.entries) if self.entries is not None else None,
            "objectives": [o.to_dict() for o in self.objectives],
            "assumptions": [a.to_dict() for a in self.assumptions],
        }


_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({RUNNING, ReliabilityStatus.FAILED.value}),
    RUNNING: frozenset(s.value for s in ReliabilityStatus),
    **{s.value: frozenset() for s in ReliabilityStatus},
}


@dataclass(frozen=True, slots=True)
class ReliabilityAnalysisError:
    """Why an analysis failed: a stable code and a sentence safe to show (never internals)."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ReliabilityAnalysis:
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    revision_number: int
    revision_content_hash: str
    status: str
    requested_by_user_id: uuid.UUID | None
    requested_at: datetime
    label: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: ReliabilityResult | None = None
    error: ReliabilityAnalysisError | None = None

    @property
    def finished(self) -> bool:
        return self.status not in (PENDING, RUNNING)

    def _move(self, to: str) -> None:
        if to not in _TRANSITIONS[self.status]:
            raise InvalidReliabilityAnalysisTransition(details={"from": self.status, "to": to})

    def start(self, at: datetime) -> ReliabilityAnalysis:
        self._move(RUNNING)
        return replace(self, status=RUNNING, started_at=at)

    def finish(self, result: ReliabilityResult, at: datetime) -> ReliabilityAnalysis:
        self._move(result.status.value)
        return replace(self, status=result.status.value, completed_at=at, result=result)

    def fail(self, error: ReliabilityAnalysisError, at: datetime) -> ReliabilityAnalysis:
        self._move(ReliabilityStatus.FAILED.value)
        return replace(self, status=ReliabilityStatus.FAILED.value, completed_at=at, error=error)
