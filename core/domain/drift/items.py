"""Drift items: a difference followed across analyses, and its review by people.

An item belongs to one architecture and correlates, by the findings' ``item_key`` (subject and
property), every analysis that detected the difference — so a later analysis never erases an
earlier one, and history stays retrievable. Its ``status`` is a person's review, kept apart from
what the comparison found (the latest finding's ``type`` and ``classification``):

- ``acknowledge``, ``investigate``: seen, being looked at — nothing about the architecture changes;
- ``accept`` (expected): the baseline is **not** updated by it;
- ``dismiss``: needs a reason;
- ``resolve``: needs evidence — a later analysis that no longer detects it, or a linked revision;
- ``reopen``: a person reopens an accepted, dismissed or resolved item (with a note); an analysis that
  detects a resolved difference again reopens it;
- ``note`` and ``link`` (a decision, migration plan, evolution analysis or revision) change no status.

Every action is an event — who (none for an analysis), when, the status before and after, the note,
the link or evidence — and the history is append-only. No action changes the architecture.
"""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from core.domain.discovery.values import MAX_REFERENCE

from .errors import InvalidReviewAction
from .findings import DriftFinding
from .values import (
    Classification,
    ElementType,
    FindingType,
    LinkKind,
    ReviewAction,
    ReviewStatus,
)

MAX_NOTE = 2000
MAX_HISTORY = 1000
A, R = ReviewAction, ReviewStatus
UNREVIEWED = frozenset({R.OPEN, R.REOPENED})
ACTIVE = UNREVIEWED | {R.ACKNOWLEDGED, R.INVESTIGATING}
CLOSED = frozenset({R.ACCEPTED, R.DISMISSED, R.RESOLVED})
# action -> (the statuses it applies to, the status it leads to: None keeps the status)
MOVES: dict[ReviewAction, tuple[frozenset[ReviewStatus], ReviewStatus | None]] = {
    A.ACKNOWLEDGE: (UNREVIEWED, R.ACKNOWLEDGED),
    A.INVESTIGATE: (UNREVIEWED | {R.ACKNOWLEDGED}, R.INVESTIGATING),
    A.ACCEPT: (ACTIVE, R.ACCEPTED),
    A.DISMISS: (ACTIVE, R.DISMISSED),
    A.RESOLVE: (ACTIVE | {R.ACCEPTED}, R.RESOLVED),
    A.REOPEN: (CLOSED, R.REOPENED),
    A.NOTE: (frozenset(ReviewStatus), None),
    A.LINK: (frozenset(ReviewStatus), None),
}
NEEDS_NOTE = frozenset({A.DISMISS, A.REOPEN, A.NOTE})


def _refuse(action: ReviewAction, status: ReviewStatus, reason: str) -> InvalidReviewAction:
    return InvalidReviewAction(details={"action": action.value, "status": status.value, "reason": reason})


@dataclass(frozen=True, slots=True)
class Link:
    """A record the item is related to — a person's statement, recorded as such."""

    kind: LinkKind
    target: str  # the decision, plan or analysis id; for a revision, the revision number
    architecture_id: uuid.UUID | None = None  # for a revision link

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LinkKind):
            raise InvalidReviewAction(details={"field": "link.kind", "reason": "invalid"})
        if not isinstance(self.target, str) or not self.target.strip() or len(self.target) > MAX_REFERENCE:
            raise InvalidReviewAction(details={"field": "link.target", "reason": "invalid"})
        if (self.kind is LinkKind.REVISION) != (self.architecture_id is not None):
            raise InvalidReviewAction(details={"field": "link.architecture_id", "reason": "revision_only"})

    def to_dict(self) -> dict[str, Any]:
        architecture = str(self.architecture_id) if self.architecture_id else None
        return {"kind": self.kind.value, "target": self.target, "architecture_id": architecture}


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    action: ReviewAction
    previous: ReviewStatus
    status: ReviewStatus
    at: datetime
    user_id: uuid.UUID | None = None  # None: recorded by an analysis
    note: str | None = None
    link: Link | None = None
    analysis_id: uuid.UUID | None = None  # the analysis that detected it, or the resolving evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "previous": self.previous.value,
            "status": self.status.value,
            "at": self.at.isoformat(),
            "user_id": str(self.user_id) if self.user_id else None,
            "note": self.note,
            "link": self.link.to_dict() if self.link else None,
            "analysis_id": str(self.analysis_id) if self.analysis_id else None,
        }


@dataclass(frozen=True, slots=True)
class DriftItem:
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    key: str  # the findings' item_key
    element: ElementType
    subject: str
    path: str | None
    type: FindingType  # the latest finding's
    classification: Classification  # the latest finding's
    first_analysis_id: uuid.UUID
    last_analysis_id: uuid.UUID  # the latest analysis that detected it
    status: ReviewStatus = ReviewStatus.OPEN
    history: tuple[ReviewEvent, ...] = ()
    links: tuple[Link, ...] = ()

    @classmethod
    def first_seen(
        cls,
        item_id: uuid.UUID,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        finding: DriftFinding,
        analysis_id: uuid.UUID,
        at: datetime,
    ) -> DriftItem:
        detected = ReviewEvent(A.DETECTED, R.OPEN, R.OPEN, at, analysis_id=analysis_id)
        return cls(
            item_id, project_id, architecture_id, finding.item_key, finding.element, finding.subject,
            finding.path, finding.type, finding.classification, analysis_id, analysis_id,
            history=(detected,),
        )  # fmt: skip

    def detected(self, finding: DriftFinding, analysis_id: uuid.UUID, at: datetime) -> DriftItem:
        """A later analysis found the difference again: recorded, and a resolved item reopens."""
        if finding.item_key != self.key:
            raise InvalidReviewAction(details={"field": "finding", "reason": "another_item"})
        status = R.REOPENED if self.status is R.RESOLVED else self.status
        event = ReviewEvent(A.DETECTED, self.status, status, at, analysis_id=analysis_id)
        return replace(
            self,
            type=finding.type,
            classification=finding.classification,
            last_analysis_id=analysis_id,
            status=status,
            history=self._appended(event),
        )

    def act(
        self,
        action: ReviewAction,
        user_id: uuid.UUID,
        at: datetime,
        *,
        note: str | None = None,
        link: Link | None = None,
        evidence_analysis_id: uuid.UUID | None = None,
    ) -> DriftItem:
        """A person's review action. Refused when the status does not allow it or what it needs is
        missing. Never changes the architecture."""
        if action not in MOVES:  # "detected" is recorded by analyses only
            raise _refuse(action, self.status, "not_a_review_action")
        allowed, to = MOVES[action]
        if self.status not in allowed:
            raise _refuse(action, self.status, "not_allowed_in_status")
        clean = note.strip() if isinstance(note, str) else None
        if note is not None and (not clean or len(clean) > MAX_NOTE):
            raise _refuse(action, self.status, "invalid_note")
        if action in NEEDS_NOTE and not clean:
            raise _refuse(action, self.status, "note_required")
        if action is A.LINK and link is None:
            raise _refuse(action, self.status, "link_required")
        revision_link = link is not None and link.kind is LinkKind.REVISION
        if action is A.RESOLVE and evidence_analysis_id is None and not revision_link:
            raise _refuse(action, self.status, "evidence_required")  # a later analysis or a revision
        status = to or self.status
        event = ReviewEvent(action, self.status, status, at, user_id, clean, link, evidence_analysis_id)
        links = (*self.links, link) if link is not None else self.links
        return replace(self, status=status, history=self._appended(event), links=links)

    def _appended(self, event: ReviewEvent) -> tuple[ReviewEvent, ...]:
        if len(self.history) >= MAX_HISTORY:
            raise _refuse(event.action, self.status, "history_full")
        return (*self.history, event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "project_id": str(self.project_id),
            "architecture_id": str(self.architecture_id),
            "key": self.key,
            "element": self.element.value,
            "subject": self.subject,
            "path": self.path,
            "type": self.type.value,
            "classification": self.classification.value,
            "first_analysis_id": str(self.first_analysis_id),
            "last_analysis_id": str(self.last_analysis_id),
            "status": self.status.value,
            "history": [e.to_dict() for e in self.history],
            "links": [link.to_dict() for link in self.links],
        }
