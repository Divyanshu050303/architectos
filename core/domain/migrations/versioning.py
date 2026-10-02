"""Migration plan versions over time: whether a version is still current, how a plan is regenerated
or revised without overwriting a reviewed version, and how a version is submitted, approved or
rejected — always this exact version.

- **Staleness is computed on read**, never stored: a version is stale when the content of its source
  or target is no longer what it was planned from (or is no longer available), when the
  architecture has a revision later than the one the plan reaches, when the planner or a pattern it
  used has another version now, or when a stored analysis it cites is no longer current. A stale
  version is still shown, with its reasons; it cannot be submitted or approved.
- **Regeneration and revision** append a version: the previous version keeps its content and its
  review history, and is marked superseded by a recorded event. A version under review or approved
  is replaced only when that is asked for explicitly; an identical regeneration creates nothing.
- **A verdict is exact**: approving or rejecting names the version number and the fingerprint of the
  proposal reviewed, and the review event records it. Reading a plan changes nothing; nothing is
  ever approved or executed automatically.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from core.domain.evolution.values import EvidenceState

from .entities import TRANSITIONS, MigrationPlanVersion, MigrationRequest
from .errors import (
    InvalidMigrationRequest,
    InvalidPlanTransition,
    PlanVersionMismatch,
    ReviewedPlanNotReplaced,
    StaleMigrationPlan,
)
from .plans import MigrationProposal
from .values import PlanStatus, TargetKind

S = PlanStatus
UNDER_REVIEW = frozenset({S.READY_FOR_REVIEW, S.APPROVED})


class StaleReason(StrEnum):
    SOURCE_CHANGED = "source_changed"  # the source revision's content differs, or it is gone
    TARGET_CHANGED = "target_changed"  # the target's content differs, or it can no longer be rebuilt
    NEWER_REVISION = "newer_revision"  # the architecture has a later revision than the plan reaches
    MODELS_CHANGED = "models_changed"  # the planner or a pattern it used has another version now
    EVIDENCE_STALE = "evidence_stale"  # a stored analysis it cited is no longer current


@dataclass(frozen=True, slots=True)
class CurrentState:
    """What the system holds now, read when a plan version is read."""

    source_hash: str | None  # the source revision's content hash now; None: no longer available
    target_hash: str | None  # the target revision's (or candidate overlay's) hash now; None: unavailable
    latest_revision: int  # the architecture's latest revision number
    models: Mapping[str, int]  # the planner's and patterns' current versions
    # each cited analysis's state now, by its key (source, reference, item)
    evidence: Mapping[tuple[str, str, str], EvidenceState] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Freshness:
    reasons: tuple[StaleReason, ...] = ()

    @property
    def stale(self) -> bool:
        return bool(self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {"stale": self.stale, "reasons": [r.value for r in self.reasons]}


def freshness(proposal: MigrationProposal, now: CurrentState) -> Freshness:
    """Whether the version is still current, and if not, why."""
    source, target = proposal.source, proposal.target
    reached = target.revision_number if target.kind is TargetKind.REVISION else source.revision_number
    cited = [e for e in proposal.evidence if e.state is EvidenceState.CURRENT]  # missing was never cited
    checks = (
        (StaleReason.SOURCE_CHANGED, now.source_hash != source.content_hash),
        (StaleReason.TARGET_CHANGED, now.target_hash != target.content_hash),
        (StaleReason.NEWER_REVISION, now.latest_revision > reached),
        (StaleReason.MODELS_CHANGED, any(now.models.get(m) != v for m, v in proposal.models.items())),
        (
            StaleReason.EVIDENCE_STALE,
            any(now.evidence.get(e.key, EvidenceState.MISSING) is not EvidenceState.CURRENT for e in cited),
        ),
    )
    return Freshness(tuple(reason for reason, found in checks if found))


def _fresh(found: Freshness) -> None:
    if found.stale:
        raise StaleMigrationPlan(details={"reasons": [r.value for r in found.reasons]})


def first_version(
    *,
    version_id: uuid.UUID,
    plan_id: uuid.UUID,
    project_id: uuid.UUID,
    request: MigrationRequest,
    proposal: MigrationProposal,
    user_id: uuid.UUID,
    at: datetime,
) -> MigrationPlanVersion:
    """Version 1 of a new plan, in the status its proposal warrants (draft or needs information)."""
    return MigrationPlanVersion(
        version_id, plan_id, 1, project_id, request, proposal, proposal.status, user_id, at
    )


@dataclass(frozen=True, slots=True)
class Regeneration:
    previous: MigrationPlanVersion  # superseded — or unchanged, when nothing was created
    created: MigrationPlanVersion | None  # None: identical to the latest version


def regenerate(
    latest: MigrationPlanVersion,
    *,
    version_id: uuid.UUID,
    request: MigrationRequest,
    proposal: MigrationProposal,
    user_id: uuid.UUID,
    at: datetime,
    replace_reviewed: bool = False,
) -> Regeneration:
    """A new version of the plan from a regenerated or revised proposal. The latest version keeps its
    content and review history and is superseded — never silently when it is under review or
    approved."""
    if request.architecture_id != latest.request.architecture_id:
        raise InvalidMigrationRequest(details={"field": "architecture_id", "reason": "another_architecture"})
    if request == latest.request and proposal.fingerprint == latest.proposal.fingerprint:
        return Regeneration(latest, None)  # nothing changed: no version to review again
    if S.SUPERSEDED not in TRANSITIONS[latest.status]:
        raise InvalidPlanTransition(details={"from": latest.status.value, "to": S.SUPERSEDED.value})
    if latest.status in UNDER_REVIEW and not replace_reviewed:
        raise ReviewedPlanNotReplaced(details={"version": latest.version, "status": latest.status.value})
    number = latest.version + 1
    previous = latest.move(S.SUPERSEDED, user_id=user_id, at=at, comment=f"Superseded by version {number}.")
    created = MigrationPlanVersion(
        version_id, latest.plan_id, number, latest.project_id, request, proposal, proposal.status, user_id, at
    )
    return Regeneration(previous, created)


def submit(
    version: MigrationPlanVersion, *, current: Freshness, user_id: uuid.UUID, at: datetime
) -> MigrationPlanVersion:
    """Submitted for review — only a current version."""
    _fresh(current)
    return version.move(S.READY_FOR_REVIEW, user_id=user_id, at=at)


def _exact(version: MigrationPlanVersion, number: int, fingerprint: str) -> None:
    if number != version.version:
        raise PlanVersionMismatch(details={"version": version.version, "reason": "version_mismatch"})
    if fingerprint != version.proposal.fingerprint:
        raise PlanVersionMismatch(details={"version": version.version, "reason": "content_mismatch"})


def approve(
    version: MigrationPlanVersion,
    *,
    version_number: int,
    fingerprint: str,
    current: Freshness,
    user_id: uuid.UUID,
    at: datetime,
    comment: str | None = None,
) -> MigrationPlanVersion:
    """Approved by a person: this exact version, while it is current. Nothing is executed."""
    _exact(version, version_number, fingerprint)
    _fresh(current)
    return version.move(S.APPROVED, user_id=user_id, at=at, comment=comment, fingerprint=fingerprint)


def reject(
    version: MigrationPlanVersion,
    *,
    version_number: int,
    fingerprint: str,
    comment: str,
    user_id: uuid.UUID,
    at: datetime,
) -> MigrationPlanVersion:
    """Rejected by a person, with feedback that the version keeps."""
    _exact(version, version_number, fingerprint)
    return version.move(S.REJECTED, user_id=user_id, at=at, comment=comment, fingerprint=fingerprint)


def archive(version: MigrationPlanVersion, *, user_id: uuid.UUID, at: datetime) -> MigrationPlanVersion:
    return version.move(S.ARCHIVED, user_id=user_id, at=at)
