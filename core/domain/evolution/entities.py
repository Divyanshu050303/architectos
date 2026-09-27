"""An evolution analysis: typed goals evaluated against one exact architecture revision (the baseline),
from the evidence the other engines stored for it, producing candidate proposals and findings.

The **request** names the exact revision, the goals, the requirements that are relevant (default:
every in-force requirement), explicit constraints the candidates must respect, an optional scope
(the nodes to consider), the stored analyses to use as evidence (default: the latest of each engine
for the architecture; only those of the baseline's exact content count), assumptions (recorded,
never computed with) and a label. It never carries the architecture's topology.

**Constraints** are explicit and evaluable: elements that must not change, candidate categories to
exclude, and a replica ceiling. A candidate a constraint excludes is reported, not silently dropped.

Lifecycle: ``pending`` → ``running`` → a final status (what the result established, or ``failed``).
Final statuses are final. An analysis never changes the architecture and never creates a revision.
"""

import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from core.domain.engine_results import Evidence

from .errors import InvalidEvolutionRequest, InvalidEvolutionTransition
from .goals import EvolutionGoal
from .results import MAX_GOALS, EvolutionResult
from .values import CandidateCategory, EvidenceSource, EvolutionStatus

KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
MAX_ELEMENT_ID = 128
MAX_SCOPE = 1000
MAX_FROZEN = 1000
MAX_REQUIREMENTS = 200
MAX_ASSUMPTIONS = 50
MAX_LABEL_LENGTH = 100
MAX_REPLICAS = 1000
PENDING, RUNNING = "pending", "running"
CITABLE = frozenset(  # the engines whose stored analyses an evolution analysis reads
    {
        EvidenceSource.CAPACITY,
        EvidenceSource.COST,
        EvidenceSource.RELIABILITY,
        EvidenceSource.SECURITY,
        EvidenceSource.OBSERVABILITY,
        EvidenceSource.VALIDATION,
    }
)


def _invalid(field: str, reason: str) -> InvalidEvolutionRequest:
    return InvalidEvolutionRequest(details={"field": field, "reason": reason})


def _element_ids(values: object, field: str, limit: int) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > limit:
        raise _invalid(field, "too_many")
    if not all(isinstance(v, str) and 0 < len(v) <= MAX_ELEMENT_ID for v in values):
        raise _invalid(field, "invalid_reference")
    return tuple(sorted(set(values)))


@dataclass(frozen=True, slots=True)
class EvolutionConstraints:
    frozen_elements: tuple[str, ...] = ()  # nodes and connections no candidate may change
    excluded_categories: tuple[CandidateCategory, ...] = ()
    max_replicas: int | None = None  # no candidate proposes more replicas than this

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "frozen_elements",
            _element_ids(self.frozen_elements, "constraints.frozen_elements", MAX_FROZEN),
        )
        excluded = self.excluded_categories
        if not isinstance(excluded, tuple) or not all(isinstance(c, CandidateCategory) for c in excluded):
            raise _invalid("constraints.excluded_categories", "unknown_category")
        object.__setattr__(self, "excluded_categories", tuple(sorted(set(excluded), key=lambda c: c.value)))
        ceiling = self.max_replicas
        if ceiling is not None and (
            isinstance(ceiling, bool) or not isinstance(ceiling, int) or not 1 <= ceiling <= MAX_REPLICAS
        ):
            raise _invalid("constraints.max_replicas", "out_of_range")

    def to_dict(self) -> dict[str, Any]:
        return {
            "frozen_elements": list(self.frozen_elements),
            "excluded_categories": [c.value for c in self.excluded_categories],
            "max_replicas": self.max_replicas,
        }


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    """A stored analysis the request names as evidence (instead of the engine's latest)."""

    source: EvidenceSource
    analysis_id: uuid.UUID

    def __post_init__(self) -> None:
        if self.source not in CITABLE:
            raise _invalid("evidence.source", "not_citable")
        if not isinstance(self.analysis_id, uuid.UUID):
            raise _invalid("evidence.analysis_id", "invalid_reference")

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source.value, "analysis_id": str(self.analysis_id)}


@dataclass(frozen=True, slots=True)
class EvolutionRequest:
    architecture_id: uuid.UUID
    revision_number: int
    goals: tuple[EvolutionGoal, ...]
    requirement_ids: tuple[uuid.UUID, ...] | None = None  # None: every in-force requirement
    constraints: EvolutionConstraints = EvolutionConstraints()
    scope: tuple[str, ...] | None = None  # the nodes to consider; None: all
    evidence: tuple[EvidenceCitation, ...] = ()  # at most one per engine; others: the latest
    assumptions: tuple[Evidence, ...] = ()  # key and statement, recorded only
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
        self._goals()
        self._requirements()
        if not isinstance(self.constraints, EvolutionConstraints):
            raise _invalid("constraints", "not_an_object")
        if self.scope is not None:
            scope = _element_ids(self.scope, "scope", MAX_SCOPE)
            if not scope:
                raise _invalid("scope", "empty")
            object.__setattr__(self, "scope", scope)
        self._evidence()
        self._assumptions()
        if self.label is not None and (
            not isinstance(self.label, str) or not self.label.strip() or len(self.label) > MAX_LABEL_LENGTH
        ):
            raise _invalid("label", "invalid_text")

    def _goals(self) -> None:
        goals = self.goals
        if not isinstance(goals, tuple) or not goals:
            raise _invalid("goals", "empty")
        if len(goals) > MAX_GOALS:
            raise _invalid("goals", "too_many")
        if not all(isinstance(g, EvolutionGoal) for g in goals):
            raise _invalid("goals", "not_a_goal")
        if len({g.key for g in goals}) != len(goals):
            raise _invalid("goals", "duplicate")
        object.__setattr__(self, "goals", tuple(sorted(goals, key=lambda g: g.key)))

    def _requirements(self) -> None:
        ids = self.requirement_ids
        if ids is None:
            return
        if not isinstance(ids, tuple) or not 0 < len(ids) <= MAX_REQUIREMENTS:
            raise _invalid("requirement_ids", "empty" if ids == () else "too_many")
        if not all(isinstance(i, uuid.UUID) for i in ids):
            raise _invalid("requirement_ids", "invalid_reference")
        object.__setattr__(self, "requirement_ids", tuple(sorted(set(ids))))

    def _evidence(self) -> None:
        cited = self.evidence
        if not isinstance(cited, tuple) or not all(isinstance(c, EvidenceCitation) for c in cited):
            raise _invalid("evidence", "not_a_citation")
        if len({c.source for c in cited}) != len(cited):
            raise _invalid("evidence", "duplicate_source")
        object.__setattr__(self, "evidence", tuple(sorted(cited, key=lambda c: c.source.value)))

    def _assumptions(self) -> None:
        assumptions = self.assumptions
        if not isinstance(assumptions, tuple) or len(assumptions) > MAX_ASSUMPTIONS:
            raise _invalid("assumptions", "too_many")
        for a in assumptions:
            if not isinstance(a, Evidence) or not isinstance(a.label, str) or not KEY.fullmatch(a.label):
                raise _invalid("assumptions.key", "invalid_key")
            if not isinstance(a.value, str) or not a.value.strip() or len(a.value) > 500:
                raise _invalid("assumptions.statement", "invalid_text")
        if len({a.label for a in assumptions}) != len(assumptions):
            raise _invalid("assumptions", "duplicate_key")
        object.__setattr__(self, "assumptions", tuple(sorted(assumptions, key=lambda a: a.label)))

    def cited(self, source: EvidenceSource) -> uuid.UUID | None:
        return next((c.analysis_id for c in self.evidence if c.source is source), None)

    def inputs(self) -> dict[str, Any]:
        """What the request contributes to the result, canonically."""
        return {
            "architecture_id": str(self.architecture_id),
            "revision_number": self.revision_number,
            "goals": [g.to_dict() for g in self.goals],
            "requirement_ids": [str(i) for i in self.requirement_ids] if self.requirement_ids else None,
            "constraints": self.constraints.to_dict(),
            "scope": list(self.scope) if self.scope is not None else None,
            "evidence": [c.to_dict() for c in self.evidence],
            "assumptions": [{"key": a.label, "statement": a.value} for a in self.assumptions],
        }


_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({RUNNING, EvolutionStatus.FAILED.value}),
    RUNNING: frozenset(s.value for s in EvolutionStatus),
    **{s.value: frozenset() for s in EvolutionStatus},
}


@dataclass(frozen=True, slots=True)
class EvolutionError:
    """Why an analysis failed: a stable code and a sentence safe to show (never internals)."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class EvolutionAnalysis:
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
    result: EvolutionResult | None = None
    error: EvolutionError | None = None

    @property
    def finished(self) -> bool:
        return self.status not in (PENDING, RUNNING)

    def _move(self, to: str) -> None:
        if to not in _TRANSITIONS[self.status]:
            raise InvalidEvolutionTransition(details={"from": self.status, "to": to})

    def start(self, at: datetime) -> EvolutionAnalysis:
        self._move(RUNNING)
        return replace(self, status=RUNNING, started_at=at)

    def finish(self, result: EvolutionResult, at: datetime) -> EvolutionAnalysis:
        self._move(result.status.value)
        return replace(self, status=result.status.value, completed_at=at, result=result)

    def fail(self, error: EvolutionError, at: datetime) -> EvolutionAnalysis:
        self._move(EvolutionStatus.FAILED.value)
        return replace(self, status=EvolutionStatus.FAILED.value, completed_at=at, error=error)
