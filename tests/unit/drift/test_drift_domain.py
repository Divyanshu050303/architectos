"""Drift domain contracts (Drift Detection Engine, phase 1): typed, validated requests, findings and
results with stable ids and canonical order; exact baseline and discovery references; compatibility
and coverage explicit; "no difference" said only of the inspected scope; no score; the review
lifecycle kept apart from the comparison, auditable, and never changing the architecture."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from core.domain.drift.analyses import (
    AnalysisError,
    BaselineRef,
    Coverage,
    DriftAnalysis,
    DriftRequest,
    DriftResult,
    ObservedRef,
)
from core.domain.drift.errors import (
    InvalidDriftRequest,
    InvalidDriftResult,
    InvalidDriftTransition,
    InvalidReviewAction,
)
from core.domain.drift.findings import CompatibilityCheck, DriftFinding
from core.domain.drift.items import DriftItem, Link
from core.domain.drift.values import (
    AnalysisStatus,
    Classification,
    Compatibility,
    ElementType,
    FindingType,
    LinkKind,
    MatchMethod,
    ReviewAction,
    ReviewStatus,
    worst,
)

ARCH, RUN, ADA = uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3)
AT = datetime(2026, 10, 2, tzinfo=UTC)
RULE = "drift-components@1"
BASELINE = BaselineRef(ARCH, 3, "a" * 64, 1)
OBSERVED = ObservedRef(RUN, "b" * 64, "c" * 64, 1, {"kubernetes": 1, "discovery-catalog": 1})
COMPATIBLE = (CompatibilityCheck("extractor_versions", Compatibility.COMPATIBLE, "Same versions."),)
INCOMPATIBLE = (CompatibilityCheck("ir_schema", Compatibility.INCOMPATIBLE, "Another schema."),)
COVERAGE = Coverage(("kubernetes",), ("k8s/shop.yaml",))
POTENTIAL = Classification.POTENTIAL


def added(subject: str = "node:cache", **extra: Any) -> DriftFinding:
    values: dict[str, Any] = {
        "discovered_key": "kubernetes:shop/deployment/cache",
        "evidence": ("dsf_1",),
        "locations": ("k8s/shop.yaml#0:metadata.name",),
    } | extra
    return DriftFinding(
        FindingType.COMPONENT_ADDED, Classification.CONFIRMED, ElementType.NODE, subject,
        "Declared in the sources; not in the baseline.", RULE, **values,
    )  # fmt: skip


def replicas(classification: Classification = Classification.CONFIRMED, **extra: Any) -> DriftFinding:
    values: dict[str, Any] = {
        "path": "configuration.replicas",
        "baseline_id": "api",
        "discovered_key": "api",
        "match": MatchMethod.SAME_ID,
        "baseline_value": 2,
        "discovered_value": 4,
        "evidence": ("dsf_2",),
    } | extra
    return DriftFinding(
        FindingType.RESOURCE_CHANGED, classification, ElementType.NODE, "node:api",
        "The declared replica count differs.", "drift-configuration@1", **values,
    )  # fmt: skip


def result(*findings: DriftFinding, **extra: Any) -> DriftResult:
    return DriftResult(BASELINE, OBSERVED, COMPATIBLE, COVERAGE, findings, **extra)


def pending() -> DriftAnalysis:
    return DriftAnalysis(
        uuid.UUID(int=9), uuid.UUID(int=8), DriftRequest(ARCH, 3, RUN), AnalysisStatus.PENDING, ADA, AT
    )


def item() -> DriftItem:
    return DriftItem.first_seen(uuid.UUID(int=5), uuid.UUID(int=8), ARCH, replicas(), uuid.UUID(int=10), AT)


def test_a_request_names_exact_references_and_no_topology() -> None:
    request = DriftRequest(ARCH, 3, RUN, exclude=("b", "a", "a"), label="Weekly")
    assert request.exclude == ("a", "b")
    fields = {"architecture_id", "baseline_revision", "discovery_run_id", "policy", "exclude", "label"}
    assert set(request.to_dict()) == fields
    base: dict[str, Any] = {"architecture_id": ARCH, "baseline_revision": 3, "discovery_run_id": RUN}
    for bad, reason in (
        ({"baseline_revision": 0}, "invalid_revision"),
        ({"policy": "lenient"}, "unknown_policy"),
        ({"exclude": ("../x",)}, "invalid_identifier"),
        ({"label": " "}, "invalid_text"),
    ):
        with pytest.raises(InvalidDriftRequest) as refused:
            DriftRequest(**(base | bad))
        assert refused.value.details["reason"] == reason


def test_finding_ids_are_stable_and_correlate_repeated_differences() -> None:
    assert added().id == added().id  # the same difference, the same id, in any analysis
    assert replicas().item_key == replicas(POTENTIAL, limitations=("x",)).item_key
    removed = DriftFinding(
        FindingType.COMPONENT_REMOVED, Classification.CONFIRMED, ElementType.NODE, "node:api",
        "No longer declared.", RULE, path="configuration.replicas", baseline_id="api",
        baseline_reference="k8s/shop.yaml#0:metadata.name",
    )  # fmt: skip
    assert removed.id != replicas().id  # another type, another finding
    assert removed.item_key == replicas().item_key  # the same subject: one drift item


def test_a_finding_rests_on_evidence_and_never_keeps_a_secret() -> None:
    with pytest.raises(InvalidDriftResult):
        added(evidence=())  # confirmed without evidence or baseline provenance
    with pytest.raises(InvalidDriftResult):
        replicas(POTENTIAL)  # not confirmed: must say what is missing
    with pytest.raises(InvalidDriftResult):
        replicas(path="configuration.password", redacted=True)  # values kept though redacted
    secret = replicas(
        path="configuration.password", redacted=True, baseline_value=None, discovered_value=None
    )
    assert (secret.baseline_value, secret.discovered_value) == (None, None)
    with pytest.raises(InvalidDriftResult):
        DriftFinding(
            FindingType.COMPONENT_REMOVED, Classification.CONFIRMED, ElementType.NODE, "node:api", "x", RULE
        )  # a removal names its baseline element


def test_a_result_is_canonical_and_says_no_difference_only_of_its_coverage() -> None:
    first, again = result(replicas(), added()), result(added(), replicas())
    assert [f.id for f in first.findings] == [f.id for f in again.findings]
    assert first.fingerprint == again.fingerprint
    assert first.summary()["types"] == {"component_added": 1, "resource_changed": 1}
    assert "score" not in str(first.to_dict())
    assert result().no_difference_within_coverage
    with pytest.raises(InvalidDriftResult):
        result(added(), added())  # one finding per difference


def test_compatibility_is_the_least_comparable_dimension_and_blocks_comparison() -> None:
    partial = worst([Compatibility.COMPATIBLE, Compatibility.PARTIALLY_COMPARABLE])
    assert partial is Compatibility.PARTIALLY_COMPARABLE
    blocked = DriftResult(BASELINE, OBSERVED, (*COMPATIBLE, *INCOMPATIBLE), COVERAGE)
    assert (blocked.status, blocked.no_difference_within_coverage) == (Compatibility.INCOMPATIBLE, False)
    with pytest.raises(InvalidDriftResult):
        DriftResult(BASELINE, OBSERVED, INCOMPATIBLE, COVERAGE, (added(),))  # nothing is compared


def test_the_analysis_lifecycle_reflects_compatibility_coverage_and_certainty() -> None:
    assert pending().start(AT).finish(result(added()), AT).status is AnalysisStatus.COMPLETED
    potential = replicas(POTENTIAL, limitations=("The identity is not confirmed.",))
    warned = AnalysisStatus.COMPLETED_WITH_WARNINGS
    assert pending().start(AT).finish(result(potential), AT).status is warned
    partial = DriftResult(BASELINE, OBSERVED, COMPATIBLE, Coverage(partial=("a.yaml",)))
    assert pending().start(AT).finish(partial, AT).status is warned
    blocked = DriftResult(BASELINE, OBSERVED, INCOMPATIBLE, COVERAGE)
    assert pending().start(AT).finish(blocked, AT).status is AnalysisStatus.INCOMPATIBLE_INPUTS
    failed = pending().start(AT).fail(AnalysisError("x", "y"), AT)
    with pytest.raises(InvalidDriftTransition):
        failed.start(AT)


def test_review_is_separate_from_the_comparison_and_auditable() -> None:
    reviewed = item().act(ReviewAction.ACKNOWLEDGE, ADA, AT)
    assert (reviewed.status, reviewed.classification) == (ReviewStatus.ACKNOWLEDGED, Classification.CONFIRMED)
    event = reviewed.history[-1]
    assert (event.action, event.previous, event.status, event.user_id) == (
        ReviewAction.ACKNOWLEDGE, ReviewStatus.OPEN, ReviewStatus.ACKNOWLEDGED, ADA,
    )  # fmt: skip
    noted = reviewed.act(ReviewAction.NOTE, ADA, AT, note="Checking with the platform team.")
    assert noted.status is ReviewStatus.ACKNOWLEDGED  # a note changes no status
    plan = Link(LinkKind.MIGRATION_PLAN, str(uuid.UUID(int=77)))
    linked = noted.act(ReviewAction.LINK, ADA, AT, link=plan)
    assert (linked.links, linked.status) == ((plan,), ReviewStatus.ACKNOWLEDGED)
    actions = [e.action for e in linked.history]
    assert actions == [ReviewAction.DETECTED, ReviewAction.ACKNOWLEDGE, ReviewAction.NOTE, ReviewAction.LINK]


def test_review_actions_need_what_they_rest_on() -> None:
    for action, note, reason in (
        (ReviewAction.DISMISS, None, "note_required"),
        (ReviewAction.RESOLVE, None, "evidence_required"),
        (ReviewAction.REOPEN, "x", "not_allowed_in_status"),  # an open item is not reopened
        (ReviewAction.DETECTED, None, "not_a_review_action"),  # only analyses record detections
    ):
        with pytest.raises(InvalidReviewAction) as refused:
            item().act(action, ADA, AT, note=note)
        assert refused.value.details["reason"] == reason
    revision = Link(LinkKind.REVISION, "4", ARCH)
    assert item().act(ReviewAction.RESOLVE, ADA, AT, link=revision).status is ReviewStatus.RESOLVED
    with pytest.raises(InvalidReviewAction):
        Link(LinkKind.DECISION, "d", ARCH)  # only a revision names an architecture


def test_a_resolved_difference_detected_again_reopens_and_history_is_kept() -> None:
    resolved = item().act(ReviewAction.RESOLVE, ADA, AT, evidence_analysis_id=uuid.UUID(int=11))
    again = resolved.detected(replicas(), uuid.UUID(int=12), AT)
    assert (again.status, again.last_analysis_id, again.first_analysis_id) == (
        ReviewStatus.REOPENED, uuid.UUID(int=12), uuid.UUID(int=10),
    )  # fmt: skip
    assert len(again.history) == 3  # detected, resolved, detected again
    accepted = item().act(ReviewAction.ACCEPT, ADA, AT).detected(replicas(), uuid.UUID(int=12), AT)
    assert accepted.status is ReviewStatus.ACCEPTED  # an expected difference stays accepted
    with pytest.raises(InvalidReviewAction):
        item().detected(added(), uuid.UUID(int=12), AT)  # another subject: another item
