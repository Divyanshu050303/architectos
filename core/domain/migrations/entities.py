"""A migration planning request, and a migration plan's versions with their review lifecycle.

**The request** names one architecture, its exact source revision, and the target — a later exact
revision of the same architecture, or an evolution candidate (analysis and candidate id) on the
source revision. It never carries topology, and it never accepts architecture JSON: the revisions
are read from the architecture's history. Goals, constraints, data requirements and assumptions are
what people state; they are recorded, and constraints and data requirements are used only as stated.

**A plan version** is immutable content (the request and the engine's proposal) with a review
status. Regenerating or revising a plan creates a new version; a reviewed or approved version is
never overwritten. Status transitions are made by people and recorded (who, when, why):

    draft ──► ready_for_review ──► approved ──► superseded ──► archived
      │              │   └──────► rejected ──┘
      │              └──────────────────────► superseded
    needs_information ──────────────────────► superseded / archived

Approval refers to this exact version and executes nothing: there is no execution status.
"""

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from core.domain.evolution.candidates import CANDIDATE_ID

from .errors import InvalidMigrationRequest, InvalidPlanTransition, PlanVersionMismatch
from .plans import MigrationProposal
from .values import CODE, MAX_TEXT, MAX_TITLE, PlanStatus

KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
MAX_GOALS = 20
MAX_STATEMENTS = 50
MAX_REQUIREMENTS = 200
MAX_ELEMENT_ID = 128
MAX_COMMENT = 2000

S = PlanStatus
TRANSITIONS: dict[PlanStatus, frozenset[PlanStatus]] = {
    S.DRAFT: frozenset({S.READY_FOR_REVIEW, S.SUPERSEDED, S.ARCHIVED}),
    S.NEEDS_INFORMATION: frozenset(
        {S.SUPERSEDED, S.ARCHIVED}
    ),  # revised or regenerated, never approved as is
    S.READY_FOR_REVIEW: frozenset({S.APPROVED, S.REJECTED, S.SUPERSEDED}),
    S.APPROVED: frozenset({S.SUPERSEDED, S.ARCHIVED}),
    S.REJECTED: frozenset({S.SUPERSEDED, S.ARCHIVED}),
    S.SUPERSEDED: frozenset({S.ARCHIVED}),
    S.ARCHIVED: frozenset(),
}
FEEDBACK_REQUIRED = frozenset({S.REJECTED})  # a rejection says why
EXACT = frozenset({S.APPROVED, S.REJECTED})  # a verdict names the exact content it reviewed


def _invalid(field: str, reason: str) -> InvalidMigrationRequest:
    return InvalidMigrationRequest(details={"field": field, "reason": reason})


def _is_text(value: object, limit: int = MAX_TEXT) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _statements(values: object, field: str, limit: int = MAX_STATEMENTS) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > limit:
        raise _invalid(field, "too_many")
    if not all(_is_text(v) for v in values):
        raise _invalid(field, "invalid_text")
    return tuple(v.strip() for v in values)


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """Which target a request asks for: a revision number, or an evolution candidate."""

    revision: int | None = None
    analysis_id: uuid.UUID | None = None
    candidate_id: str | None = None

    def __post_init__(self) -> None:
        by_revision = self.revision is not None
        by_candidate = self.analysis_id is not None or self.candidate_id is not None
        if by_revision == by_candidate:
            raise _invalid("target", "one_of_revision_or_candidate")
        if by_revision and not _is_count(self.revision):
            raise _invalid("target.revision", "invalid_revision")
        if by_candidate:
            if not isinstance(self.analysis_id, uuid.UUID):
                raise _invalid("target.analysis_id", "required")
            if not isinstance(self.candidate_id, str) or not CANDIDATE_ID.fullmatch(self.candidate_id):
                raise _invalid("target.candidate_id", "invalid_candidate")

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "analysis_id": str(self.analysis_id) if self.analysis_id else None,
            "candidate_id": self.candidate_id,
        }


@dataclass(frozen=True, slots=True)
class MigrationConstraints:
    """What the people planning the migration state. ``None``: not stated (never assumed)."""

    downtime_allowed: bool | None = None
    maintenance_window: str | None = None  # as stated, e.g. "Sunday 02:00-04:00 UTC"
    statements: tuple[str, ...] = ()  # other constraints, recorded as stated

    def __post_init__(self) -> None:
        if self.downtime_allowed is not None and not isinstance(self.downtime_allowed, bool):
            raise _invalid("constraints.downtime_allowed", "not_a_boolean")
        if self.maintenance_window is not None and not _is_text(self.maintenance_window, MAX_TITLE):
            raise _invalid("constraints.maintenance_window", "invalid_text")
        object.__setattr__(self, "statements", _statements(self.statements, "constraints.statements"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "downtime_allowed": self.downtime_allowed,
            "maintenance_window": self.maintenance_window,
            "statements": list(self.statements),
        }


@dataclass(frozen=True, slots=True)
class DataRequirement:
    """A stated requirement about one element's data (scope, retention, verification)."""

    element_id: str
    statement: str

    def __post_init__(self) -> None:
        if not _is_text(self.element_id, MAX_ELEMENT_ID):
            raise _invalid("data_requirements.element_id", "invalid_reference")
        if not _is_text(self.statement):
            raise _invalid("data_requirements.statement", "invalid_text")

    def to_dict(self) -> dict[str, str]:
        return {"element_id": self.element_id, "statement": self.statement}


@dataclass(frozen=True, slots=True)
class Assumption:
    key: str
    statement: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not KEY.fullmatch(self.key):
            raise _invalid("assumptions.key", "invalid_key")
        if not _is_text(self.statement):
            raise _invalid("assumptions.statement", "invalid_text")

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "statement": self.statement}


@dataclass(frozen=True, slots=True)
class MigrationRequest:
    architecture_id: uuid.UUID
    source_revision: int
    target: TargetSpec
    goals: tuple[str, ...] = ()  # the migration's intent, as stated
    constraints: MigrationConstraints = field(default_factory=MigrationConstraints)
    data_requirements: tuple[DataRequirement, ...] = ()
    requirement_ids: tuple[uuid.UUID, ...] | None = None  # None: every in-force requirement
    strategy: str | None = None  # a preferred pattern id; honoured only when its prerequisites hold
    assumptions: tuple[Assumption, ...] = ()
    title: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.architecture_id, uuid.UUID):
            raise _invalid("architecture_id", "required")
        if not _is_count(self.source_revision):
            raise _invalid("source_revision", "invalid_revision")
        if not isinstance(self.target, TargetSpec):
            raise _invalid("target", "required")
        if self.target.revision is not None and self.target.revision <= self.source_revision:
            raise _invalid(
                "target.revision", "not_after_the_source"
            )  # a later revision of the same architecture
        object.__setattr__(self, "goals", _statements(self.goals, "goals", MAX_GOALS))
        if not isinstance(self.constraints, MigrationConstraints):
            raise _invalid("constraints", "invalid")
        self._check_lists()
        if self.strategy is not None and (
            not isinstance(self.strategy, str) or not CODE.fullmatch(self.strategy)
        ):
            raise _invalid("strategy", "invalid_strategy")
        if self.title is not None and not _is_text(self.title, MAX_TITLE):
            raise _invalid("title", "invalid_text")

    def _check_lists(self) -> None:
        data = self.data_requirements
        if not isinstance(data, tuple) or len(data) > MAX_STATEMENTS:
            raise _invalid("data_requirements", "too_many")
        if not all(isinstance(d, DataRequirement) for d in data):
            raise _invalid("data_requirements", "invalid")
        ids = self.requirement_ids
        if ids is not None:
            if not isinstance(ids, tuple) or len(ids) > MAX_REQUIREMENTS:
                raise _invalid("requirement_ids", "too_many")
            if not all(isinstance(i, uuid.UUID) for i in ids):
                raise _invalid("requirement_ids", "invalid_reference")
            object.__setattr__(self, "requirement_ids", tuple(sorted(set(ids))))
        assumptions = self.assumptions
        if not isinstance(assumptions, tuple) or len(assumptions) > MAX_STATEMENTS:
            raise _invalid("assumptions", "too_many")
        if not all(isinstance(a, Assumption) for a in assumptions):
            raise _invalid("assumptions", "invalid")
        if len({a.key for a in assumptions}) != len(assumptions):
            raise _invalid("assumptions", "duplicate")
        object.__setattr__(self, "assumptions", tuple(sorted(assumptions, key=lambda a: a.key)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture_id": str(self.architecture_id),
            "source_revision": self.source_revision,
            "target": self.target.to_dict(),
            "goals": list(self.goals),
            "constraints": self.constraints.to_dict(),
            "data_requirements": [d.to_dict() for d in self.data_requirements],
            "requirement_ids": None
            if self.requirement_ids is None
            else [str(i) for i in self.requirement_ids],
            "strategy": self.strategy,
            "assumptions": [a.to_dict() for a in self.assumptions],
            "title": self.title,
        }


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    """A person's action on a plan version: who, when, from which status to which, and why — and for
    an approval or a rejection, the fingerprint of the exact proposal reviewed."""

    from_status: PlanStatus
    to_status: PlanStatus
    user_id: uuid.UUID
    at: datetime
    comment: str | None = None
    fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "user_id": str(self.user_id),
            "at": self.at.isoformat(),
            "comment": self.comment,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class MigrationPlanVersion:
    """One immutable version of a migration plan and its review status."""

    id: uuid.UUID  # this version's id
    plan_id: uuid.UUID  # the plan every version of it shares
    version: int
    project_id: uuid.UUID
    request: MigrationRequest
    proposal: MigrationProposal
    status: PlanStatus
    created_by_user_id: uuid.UUID
    created_at: datetime
    reviews: tuple[ReviewEvent, ...] = ()

    def __post_init__(self) -> None:
        if not _is_count(self.version):
            raise _invalid("version", "invalid_version")
        if self.request.architecture_id != self.proposal.source.architecture_id:
            raise _invalid("request", "does_not_match_the_proposal")
        if self.request.source_revision != self.proposal.source.revision_number:
            raise _invalid("request", "does_not_match_the_proposal")

    @property
    def title(self) -> str:
        return self.request.title or f"Migration from revision {self.proposal.source.revision_number}"

    def move(
        self,
        to: PlanStatus,
        *,
        user_id: uuid.UUID,
        at: datetime,
        comment: str | None = None,
        fingerprint: str | None = None,
    ) -> MigrationPlanVersion:
        """A person's transition, recorded. Refused when the lifecycle does not allow it; an approval
        or a rejection must name the fingerprint of this version's exact proposal."""
        if to not in TRANSITIONS[self.status]:
            raise InvalidPlanTransition(details={"from": self.status.value, "to": to.value})
        if to in EXACT and fingerprint != self.proposal.fingerprint:
            raise PlanVersionMismatch(details={"version": self.version, "reason": "content_mismatch"})
        if comment is not None and not _is_text(comment, MAX_COMMENT):
            raise _invalid("comment", "invalid_text")
        if to in FEEDBACK_REQUIRED and not comment:
            raise _invalid("comment", "required")
        event = ReviewEvent(self.status, to, user_id, at, comment, fingerprint if to in EXACT else None)
        return replace(self, status=to, reviews=(*self.reviews, event))

    @property
    def approved_by(self) -> ReviewEvent | None:
        return next((r for r in reversed(self.reviews) if r.to_status is PlanStatus.APPROVED), None)
