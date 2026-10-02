"""Plan versioning and review (Migration Planning, phase 8): distinguishable, append-only versions;
staleness computed on read from the source, target, models and evidence; regeneration that never
silently replaces a reviewed or approved version; verdicts on the exact version; review history and
feedback preserved; nothing approved or executed automatically."""

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from core.domain.evolution.candidates import EvidenceRef
from core.domain.evolution.values import EvidenceSource, EvidenceState
from core.domain.migrations.entities import MigrationPlanVersion, MigrationRequest, TargetSpec
from core.domain.migrations.errors import (
    InvalidMigrationRequest,
    InvalidPlanTransition,
    PlanVersionMismatch,
    ReviewedPlanNotReplaced,
    StaleMigrationPlan,
)
from core.domain.migrations.plans import MigrationProposal, TargetRef
from core.domain.migrations.values import PlanStatus, TargetKind
from core.domain.migrations.versioning import (
    CurrentState,
    Freshness,
    Regeneration,
    StaleReason,
    approve,
    archive,
    first_version,
    freshness,
    regenerate,
    reject,
    submit,
)
from tests.unit.migration.test_migration_changes import ARCHITECTURE, changed, shop
from tests.unit.migration.test_migration_data_downtime import plan
from tests.unit.migration.test_migration_patterns import mysql

ADA, BOB = uuid.UUID(int=10), uuid.UUID(int=11)
PLAN, PROJECT = uuid.UUID(int=30), uuid.UUID(int=31)
AT = datetime(2026, 10, 1, 9, tzinfo=UTC)
LATER = datetime(2026, 10, 1, 10, tzinfo=UTC)
CURRENT = Freshness()
S = PlanStatus


def request(**overrides: Any) -> MigrationRequest:
    return MigrationRequest(ARCHITECTURE, 1, TargetSpec(revision=2), **overrides)


def proposal(replicas: int = 4) -> MigrationProposal:
    return plan(shop(api=changed("api", replicas=replicas)))


def version(status: PlanStatus | None = None, of: MigrationProposal | None = None) -> MigrationPlanVersion:
    found = first_version(
        version_id=uuid.UUID(int=40), plan_id=PLAN, project_id=PROJECT, request=request(),
        proposal=of or proposal(), user_id=ADA, at=AT,
    )  # fmt: skip
    return dataclasses.replace(found, status=status or found.status)


def now(of: MigrationProposal, **overrides: Any) -> CurrentState:
    fields: dict[str, Any] = {
        "source_hash": of.source.content_hash,
        "target_hash": of.target.content_hash,
        "latest_revision": 2,
        "models": dict(of.models),
    }
    return CurrentState(**(fields | overrides))


def again(latest: MigrationPlanVersion, **overrides: Any) -> Regeneration:
    fields: dict[str, Any] = {
        "version_id": uuid.UUID(int=41), "request": latest.request, "proposal": proposal(5),
        "user_id": BOB, "at": LATER,
    }  # fmt: skip
    return regenerate(latest, **(fields | overrides))


def submitted() -> MigrationPlanVersion:
    return submit(version(), current=CURRENT, user_id=ADA, at=AT)


# --- versions and staleness ------------------------------------------------------------------------


def test_a_first_version_takes_the_status_its_proposal_warrants() -> None:
    assert (version().version, version().status, version().reviews) == (1, S.DRAFT, ())
    assert version(of=plan(mysql())).status is S.NEEDS_INFORMATION  # downtime and data unstated


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"source_hash": "0" * 64}, StaleReason.SOURCE_CHANGED),
        ({"source_hash": None}, StaleReason.SOURCE_CHANGED),  # no longer available
        ({"target_hash": None}, StaleReason.TARGET_CHANGED),
        ({"latest_revision": 3}, StaleReason.NEWER_REVISION),
        ({"models": {"migration_planner": 2}}, StaleReason.MODELS_CHANGED),
    ],
)
def test_a_version_is_stale_when_what_it_was_planned_from_changed(
    overrides: dict[str, Any], reason: StaleReason
) -> None:
    of = proposal()
    assert freshness(of, now(of)) == CURRENT
    if "models" in overrides:
        overrides = {"models": {**of.models, **overrides["models"]}}
    assert freshness(of, now(of, **overrides)).reasons == (reason,)


def test_cited_evidence_that_is_no_longer_current_makes_a_version_stale() -> None:
    cited = EvidenceRef(EvidenceSource.CAPACITY, "a1", EvidenceState.CURRENT, None, 2, "1" * 64)
    missing = EvidenceRef(EvidenceSource.COST, "c1", EvidenceState.MISSING, None, None, None)
    of = dataclasses.replace(proposal(), evidence=(cited, missing))
    assert (
        freshness(of, now(of, evidence={cited.key: EvidenceState.CURRENT})) == CURRENT
    )  # missing: never cited
    stale = freshness(of, now(of, evidence={cited.key: EvidenceState.STALE}))
    assert stale.reasons == (StaleReason.EVIDENCE_STALE,)
    assert freshness(of, now(of)).reasons == (StaleReason.EVIDENCE_STALE,)  # no longer found
    assert stale.to_dict() == {"stale": True, "reasons": ["evidence_stale"]}


def test_a_candidate_plan_reaches_its_source_revision() -> None:
    of = proposal()
    target = TargetRef(
        TargetKind.CANDIDATE, ARCHITECTURE, 1, of.target.content_hash, uuid.UUID(int=9), "evo_" + "a" * 20
    )
    candidate = dataclasses.replace(of, target=target)
    assert freshness(candidate, now(candidate, latest_revision=1)) == CURRENT
    assert freshness(candidate, now(candidate, latest_revision=2)).reasons == (StaleReason.NEWER_REVISION,)


def test_reading_a_version_changes_nothing() -> None:
    draft = version()
    before = (draft.status, draft.reviews, draft.proposal.fingerprint)
    freshness(draft.proposal, now(draft.proposal, latest_revision=3))
    draft.proposal.to_dict()
    assert (draft.status, draft.reviews, draft.proposal.fingerprint) == before


# --- regeneration and revision ---------------------------------------------------------------------


def test_regeneration_appends_a_version_and_supersedes_the_previous_one() -> None:
    draft = version()
    result = again(draft)
    assert result.created is not None
    assert (result.created.version, result.created.plan_id, result.created.status) == (2, PLAN, S.DRAFT)
    assert result.created.reviews == ()  # independently reviewable
    assert result.previous.status is S.SUPERSEDED
    assert result.previous.proposal is draft.proposal  # its content is kept
    [event] = result.previous.reviews
    assert (event.user_id, event.at, event.comment) == (BOB, LATER, "Superseded by version 2.")
    assert result.created.proposal.fingerprint != result.previous.proposal.fingerprint  # distinguishable


def test_an_identical_regeneration_creates_nothing() -> None:
    draft = version()
    result = again(draft, proposal=draft.proposal)
    assert (result.created, result.previous) == (None, draft)


def test_a_revised_request_is_a_new_version() -> None:
    draft = version()
    result = again(draft, request=request(title="Scale the API"), proposal=draft.proposal)
    assert result.created is not None
    assert result.created.title == "Scale the API"


@pytest.mark.parametrize("status", [S.READY_FOR_REVIEW, S.APPROVED])
def test_a_version_under_review_or_approved_is_never_replaced_silently(status: PlanStatus) -> None:
    reviewed = version(status)
    with pytest.raises(ReviewedPlanNotReplaced) as error:
        again(reviewed)
    assert error.value.details == {"version": 1, "status": status.value}
    replaced = again(reviewed, replace_reviewed=True)
    assert replaced.previous.status is S.SUPERSEDED
    assert replaced.created is not None


def test_a_rejected_version_keeps_its_feedback_when_revised() -> None:
    under_review = submitted()
    rejected = reject(
        under_review, version_number=1, fingerprint=under_review.proposal.fingerprint,
        comment="Add a rollback plan for the database.", user_id=BOB, at=AT,
    )  # fmt: skip
    revised = again(rejected)
    assert revised.created is not None
    assert [r.comment for r in revised.previous.reviews] == [
        None, "Add a rollback plan for the database.", "Superseded by version 2.",
    ]  # fmt: skip
    assert revised.created.status is S.DRAFT


def test_archived_or_superseded_versions_are_not_regenerated_and_architectures_never_mix() -> None:
    for status in (S.ARCHIVED, S.SUPERSEDED):
        with pytest.raises(InvalidPlanTransition):
            again(version(status))
    other = MigrationRequest(uuid.UUID(int=99), 1, TargetSpec(revision=2))
    with pytest.raises(InvalidMigrationRequest):
        again(version(), request=other)


# --- review ----------------------------------------------------------------------------------------


def test_only_a_current_version_is_submitted_or_approved() -> None:
    stale = Freshness((StaleReason.NEWER_REVISION,))
    with pytest.raises(StaleMigrationPlan) as error:
        submit(version(), current=stale, user_id=ADA, at=AT)
    assert error.value.details == {"reasons": ["newer_revision"]}
    under_review = submitted()
    exact = under_review.proposal.fingerprint
    with pytest.raises(StaleMigrationPlan):
        approve(under_review, version_number=1, fingerprint=exact, current=stale, user_id=BOB, at=AT)


@pytest.mark.parametrize(("number", "reason"), [(2, "version_mismatch"), (1, "content_mismatch")])
def test_approval_must_name_the_exact_version(number: int, reason: str) -> None:
    under_review = submitted()
    fingerprint = under_review.proposal.fingerprint if reason == "version_mismatch" else "0" * 64
    with pytest.raises(PlanVersionMismatch) as error:
        approve(
            under_review, version_number=number, fingerprint=fingerprint, current=CURRENT, user_id=BOB, at=AT
        )
    assert error.value.details == {"version": 1, "reason": reason}


def test_an_approval_records_who_when_and_the_exact_content() -> None:
    under_review = submitted()
    exact = under_review.proposal.fingerprint
    approved = approve(
        under_review, version_number=1, fingerprint=exact, current=CURRENT, user_id=BOB, at=LATER
    )
    assert approved.status is S.APPROVED
    assert approved.approved_by is not None
    assert (approved.approved_by.user_id, approved.approved_by.at, approved.approved_by.fingerprint) == (
        BOB, LATER, exact,
    )  # fmt: skip
    assert approved.proposal is under_review.proposal  # approval changes no content and executes nothing


def test_a_verdict_cannot_bypass_review_or_the_exact_version() -> None:
    with pytest.raises(PlanVersionMismatch):
        submitted().move(S.APPROVED, user_id=BOB, at=AT)  # no fingerprint named
    draft = version()
    with pytest.raises(InvalidPlanTransition):  # never approved without review
        approve(
            draft,
            version_number=1,
            fingerprint=draft.proposal.fingerprint,
            current=CURRENT,
            user_id=BOB,
            at=AT,
        )
    with pytest.raises(InvalidPlanTransition):  # a plan missing information is revised, never submitted
        submit(version(of=plan(mysql())), current=CURRENT, user_id=ADA, at=AT)


def test_a_rejection_keeps_its_feedback_and_the_content_reviewed() -> None:
    under_review = submitted()
    exact = under_review.proposal.fingerprint
    with pytest.raises(InvalidMigrationRequest):
        reject(under_review, version_number=1, fingerprint=exact, comment="", user_id=BOB, at=AT)
    rejected = reject(
        under_review, version_number=1, fingerprint=exact, comment="Too risky.", user_id=BOB, at=AT
    )
    assert (rejected.reviews[-1].comment, rejected.reviews[-1].fingerprint) == ("Too risky.", exact)
    assert archive(rejected, user_id=ADA, at=LATER).reviews[:-1] == rejected.reviews  # history kept
