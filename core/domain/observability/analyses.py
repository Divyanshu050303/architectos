"""An observability analysis: one execution of the observability analyzers against one architecture
revision.

The **request** names the exact revision, the **scope** (node ids whose observability is analyzed,
with the connections touching them; default the whole architecture), which **analyzers** run
(default all registered), which in-force **requirements** to evaluate (default all), and
analysis-level assumptions (recorded, never computed with). Observability inputs are not in the
request: they are properties of the architecture itself, and the policy is the project's (recorded
with the analysis). Nothing is filled in by default, and no telemetry is read.

Lifecycle: ``pending`` → ``running`` → a final status (what the result established, or ``failed``).
Final statuses are final. Analyses reference the revision (number and content hash); they never copy
it.
"""

import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .errors import InvalidObservabilityAnalysisTransition, InvalidObservabilityRequest
from .results import ObservabilityResult, ObservabilityStatus

KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
ANALYZER_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
MAX_ASSUMPTIONS = 50
MAX_ANALYZERS = 50
MAX_REQUIREMENTS = 200
MAX_SCOPE = 200
MAX_ELEMENT_ID = 128
MAX_LABEL_LENGTH = 100
PENDING, RUNNING = "pending", "running"


def _invalid(field: str, reason: str) -> InvalidObservabilityRequest:
    return InvalidObservabilityRequest(details={"field": field, "reason": reason})


@dataclass(frozen=True, slots=True)
class ObservabilityAssumption:
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
class ObservabilityAnalysisRequest:
    architecture_id: uuid.UUID
    revision_number: int
    scope: tuple[str, ...] = ()  # node ids; empty: the whole architecture
    analyzers: tuple[str, ...] | None = None  # analyzer ids; None: every registered analyzer
    requirement_ids: tuple[uuid.UUID, ...] | None = None  # in-force requirements to evaluate; None: all
    assumptions: tuple[ObservabilityAssumption, ...] = ()
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
        self._scope()
        self._analyzers()
        self._requirements()
        self._assumptions()
        if self.label is not None and (
            not isinstance(self.label, str) or not self.label.strip() or len(self.label) > MAX_LABEL_LENGTH
        ):
            raise _invalid("label", "invalid_text")

    def _scope(self) -> None:
        scope = self.scope
        if not isinstance(scope, tuple) or len(scope) > MAX_SCOPE:
            raise _invalid("scope", "too_many")
        if not all(isinstance(v, str) and 0 < len(v) <= MAX_ELEMENT_ID for v in scope):
            raise _invalid("scope", "invalid_reference")
        object.__setattr__(self, "scope", tuple(sorted(set(scope))))

    def _analyzers(self) -> None:
        analyzers = self.analyzers
        if analyzers is None:
            return
        if not isinstance(analyzers, tuple) or len(analyzers) > MAX_ANALYZERS:
            raise _invalid("analyzers", "too_many")
        if not analyzers:
            raise _invalid("analyzers", "empty")
        if not all(isinstance(a, str) and ANALYZER_ID.fullmatch(a) for a in analyzers):
            raise _invalid("analyzers", "invalid_reference")
        object.__setattr__(self, "analyzers", tuple(sorted(set(analyzers))))

    def _requirements(self) -> None:
        ids = self.requirement_ids
        if ids is None:
            return
        if not isinstance(ids, tuple) or len(ids) > MAX_REQUIREMENTS:
            raise _invalid("requirement_ids", "too_many")
        if not ids:
            raise _invalid("requirement_ids", "empty")
        if not all(isinstance(i, uuid.UUID) for i in ids):
            raise _invalid("requirement_ids", "invalid_reference")
        object.__setattr__(self, "requirement_ids", tuple(sorted(set(ids))))

    def _assumptions(self) -> None:
        assumptions = self.assumptions
        if not isinstance(assumptions, tuple) or len(assumptions) > MAX_ASSUMPTIONS:
            raise _invalid("assumptions", "too_many")
        if not all(isinstance(a, ObservabilityAssumption) for a in assumptions):
            raise _invalid("assumptions", "invalid_assumption")
        keys = [a.key for a in assumptions]
        if len(keys) != len(set(keys)):
            raise _invalid("assumptions", "duplicate_key")
        object.__setattr__(self, "assumptions", tuple(sorted(assumptions, key=lambda a: a.key)))

    def inputs(self) -> dict[str, Any]:
        """What the request contributes to the result, canonically (the policy and the
        requirements read are added by the service)."""
        return {
            "architecture_id": str(self.architecture_id),
            "revision_number": self.revision_number,
            "scope": list(self.scope),
            "analyzers": list(self.analyzers) if self.analyzers is not None else None,
            "requirement_ids": [str(i) for i in self.requirement_ids]
            if self.requirement_ids is not None
            else None,
            "assumptions": [a.to_dict() for a in self.assumptions],
        }


_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({RUNNING, ObservabilityStatus.FAILED.value}),
    RUNNING: frozenset(s.value for s in ObservabilityStatus),
    **{s.value: frozenset() for s in ObservabilityStatus},
}


@dataclass(frozen=True, slots=True)
class ObservabilityAnalysisError:
    """Why an analysis failed: a stable code and a sentence safe to show (never internals)."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ObservabilityAnalysis:
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
    result: ObservabilityResult | None = None
    error: ObservabilityAnalysisError | None = None

    @property
    def finished(self) -> bool:
        return self.status not in (PENDING, RUNNING)

    def _move(self, to: str) -> None:
        if to not in _TRANSITIONS[self.status]:
            raise InvalidObservabilityAnalysisTransition(details={"from": self.status, "to": to})

    def start(self, at: datetime) -> ObservabilityAnalysis:
        self._move(RUNNING)
        return replace(self, status=RUNNING, started_at=at)

    def finish(self, result: ObservabilityResult, at: datetime) -> ObservabilityAnalysis:
        self._move(result.status.value)
        return replace(self, status=result.status.value, completed_at=at, result=result)

    def fail(self, error: ObservabilityAnalysisError, at: datetime) -> ObservabilityAnalysis:
        self._move(ObservabilityStatus.FAILED.value)
        return replace(self, status=ObservabilityStatus.FAILED.value, completed_at=at, error=error)
