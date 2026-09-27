"""Architecture decision records: the options considered for a goal, and a person's decision.

Lifecycle:

- ``proposed``: drafted (e.g. from an evolution analysis) with its context, the options considered
  (candidate snapshots, by their stable ids), the goals, evidence and assumptions. Nothing is decided.
- ``accepted``: a person chose one of the options, with a rationale. Accepting changes nothing in the
  architecture: applying the option is a separate, authorized change (a new revision made by a
  person), which may afterwards be **linked** to the decision (``resulting_revision``) — a person's
  statement, recorded as such, never inferred.
- ``rejected``: a person rejected every option, with a rationale.
- ``superseded``: an accepted decision replaced by a later one.

The engine never accepts, rejects or links: every transition names the person who made it.
"""

import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from core.domain.engine_results import Evidence
from core.domain.evolution.candidates import BaselineRef, Candidate, EvidenceRef

from .errors import InvalidDecision, InvalidDecisionTransition

MAX_TITLE = 200
MAX_TEXT = 10_000
MAX_OPTIONS = 50
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
NOT_ACCEPTABLE = frozenset({"invalid", "unsupported"})


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


_TRANSITIONS = {
    DecisionStatus.PROPOSED: frozenset({DecisionStatus.ACCEPTED, DecisionStatus.REJECTED}),
    DecisionStatus.ACCEPTED: frozenset({DecisionStatus.SUPERSEDED}),
    DecisionStatus.REJECTED: frozenset(),
    DecisionStatus.SUPERSEDED: frozenset(),
}


def _invalid(field: str, reason: str) -> InvalidDecision:
    return InvalidDecision(details={"field": field, "reason": reason})


def _text(value: object, field: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise _invalid(field, "invalid_text")
    return value.strip()


@dataclass(frozen=True, slots=True)
class DecisionOption:
    """One option considered: a candidate, by its stable id, as it was when the decision was drafted
    (what it changes, its validation state, and its direction per dimension — no score)."""

    candidate_id: str
    title: str
    category: str
    changes: tuple[str, ...]  # "element.property = value"
    validation: str
    goals: tuple[str, ...]
    consequences: tuple[tuple[str, str], ...]  # (dimension, direction)

    @classmethod
    def of(cls, candidate: Candidate) -> DecisionOption:
        return cls(
            candidate.id,
            candidate.title,
            candidate.category.value,
            tuple(f"{c.element_id}.{c.property} = {c.to_dict()['value']}" for c in candidate.changes),
            candidate.validation.value,
            candidate.goals,
            tuple((c.dimension, c.direction.value) for c in candidate.consequences),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "title": self.title,
            "category": self.category,
            "changes": list(self.changes),
            "validation": self.validation,
            "goals": list(self.goals),
            "consequences": [{"dimension": d, "direction": v} for d, v in self.consequences],
        }

    @classmethod
    def from_dict(cls, data: Any) -> DecisionOption:
        return cls(
            data["candidate_id"],
            data["title"],
            data["category"],
            tuple(data["changes"]),
            data["validation"],
            tuple(data["goals"]),
            tuple((c["dimension"], c["direction"]) for c in data["consequences"]),
        )


@dataclass(frozen=True, slots=True)
class DecisionSource:
    """The evolution analysis a decision was drafted from, and the baseline it considered."""

    analysis_id: uuid.UUID
    baseline: BaselineRef
    model_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": str(self.analysis_id),
            "baseline": self.baseline.to_dict(),
            "model_version": self.model_version,
        }

    @classmethod
    def from_dict(cls, data: Any) -> DecisionSource:
        return cls(
            uuid.UUID(data["analysis_id"]), BaselineRef.from_dict(data["baseline"]), data["model_version"]
        )


@dataclass(frozen=True, slots=True)
class ResultingRevision:
    """The revision a person says implements the decision (a separate, authorized change)."""

    number: int
    content_hash: str
    linked_by_user_id: uuid.UUID
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class Decision:
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    number: int  # per project: ADR-<number>
    title: str
    status: DecisionStatus
    context: str
    options: tuple[DecisionOption, ...]
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    source: DecisionSource | None = None
    goals: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    assumptions: tuple[Evidence, ...] = ()
    related_element_ids: tuple[str, ...] = ()
    chosen_option: str | None = None  # a candidate id among the options
    rationale: str | None = None
    decided_by_user_id: uuid.UUID | None = None
    decided_at: datetime | None = None
    superseded_by: uuid.UUID | None = None
    resulting_revision: ResultingRevision | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _text(self.title, "title", MAX_TITLE))
        object.__setattr__(self, "context", _text(self.context, "context"))
        if isinstance(self.number, bool) or not isinstance(self.number, int) or self.number < 1:
            raise _invalid("number", "not_a_positive_count")
        if not isinstance(self.options, tuple) or len(self.options) > MAX_OPTIONS:
            raise _invalid("options", "too_many")
        if len({o.candidate_id for o in self.options}) != len(self.options):
            raise _invalid("options", "duplicate")
        object.__setattr__(
            self, "options", tuple(sorted(self.options, key=lambda o: (o.category, o.candidate_id)))
        )
        object.__setattr__(self, "goals", tuple(sorted(set(self.goals))))
        object.__setattr__(self, "related_element_ids", tuple(sorted(set(self.related_element_ids))))
        object.__setattr__(self, "evidence", tuple(sorted(set(self.evidence), key=lambda e: e.key)))
        if self.chosen_option is not None and self.chosen_option not in {
            o.candidate_id for o in self.options
        }:
            raise _invalid("chosen_option", "not_an_option")

    @property
    def reference(self) -> str:
        return f"ADR-{self.number}"

    def option(self, candidate_id: str) -> DecisionOption | None:
        return next((o for o in self.options if o.candidate_id == candidate_id), None)

    def _move(self, to: DecisionStatus) -> None:
        if to not in _TRANSITIONS[self.status]:
            raise InvalidDecisionTransition(details={"from": self.status.value, "to": to.value})

    def accept(self, candidate_id: str, rationale: str, user_id: uuid.UUID, at: datetime) -> Decision:
        """A person chose ``candidate_id``. Nothing in the architecture changes."""
        self._move(DecisionStatus.ACCEPTED)
        option = self.option(candidate_id)
        if option is None:
            raise _invalid("chosen_option", "not_an_option")
        if option.validation in NOT_ACCEPTABLE:  # it cannot be applied as proposed
            raise _invalid("chosen_option", f"option_{option.validation}")
        return replace(
            self,
            status=DecisionStatus.ACCEPTED,
            chosen_option=candidate_id,
            rationale=_text(rationale, "rationale"),
            decided_by_user_id=user_id,
            decided_at=at,
        )

    def reject(self, rationale: str, user_id: uuid.UUID, at: datetime) -> Decision:
        """A person rejected every option."""
        self._move(DecisionStatus.REJECTED)
        return replace(
            self,
            status=DecisionStatus.REJECTED,
            rationale=_text(rationale, "rationale"),
            decided_by_user_id=user_id,
            decided_at=at,
        )

    def supersede(self, by: uuid.UUID) -> Decision:
        """Replaced by a later decision of the same project."""
        if by == self.id:
            raise _invalid("superseded_by", "self")
        self._move(DecisionStatus.SUPERSEDED)
        return replace(self, status=DecisionStatus.SUPERSEDED, superseded_by=by)

    def link_revision(self, number: int, content_hash: str, user_id: uuid.UUID, at: datetime) -> Decision:
        """A person states that revision ``number`` implements the accepted decision. Recorded as
        their statement; the engine never infers or verifies it."""
        if self.status is not DecisionStatus.ACCEPTED:
            raise InvalidDecisionTransition(details={"from": self.status.value, "to": "linked"})
        if self.resulting_revision is not None:
            raise _invalid("resulting_revision", "already_linked")
        baseline = self.source.baseline.revision_number if self.source else 0
        if isinstance(number, bool) or not isinstance(number, int) or number <= baseline:
            raise _invalid("resulting_revision", "not_after_the_baseline")
        if not isinstance(content_hash, str) or not FINGERPRINT.fullmatch(content_hash):
            raise _invalid("resulting_revision", "invalid_content_hash")
        return replace(self, resulting_revision=ResultingRevision(number, content_hash, user_id, at))
