import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.domain.errors import NothingToUpdate
from core.domain.requirements.entities import (
    NewRequirement,
    Requirement,
    RequirementChanges,
    RequirementContent,
)
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementSource,
    RequirementStatus,
    RequirementType,
)
from core.domain.requirements.errors import (
    ChangeReasonRequired,
    InvalidRequirement,
    InvalidStatusTransition,
    RequirementLocked,
    RequirementNotFound,
    RequirementVersionConflict,
)
from core.domain.requirements.value_objects import QuantityConstraint

NOW = datetime(2026, 9, 25, tzinfo=UTC)
ADA = uuid.uuid7()
RPS_2K: dict[str, Any] = {
    "metric": "requests_per_second",
    "operator": ">=",
    "value": 2000,
    "unit": "requests/second",
}
RPS_5K = RPS_2K | {"value": 5000}
S = RequirementStatus


def new(**overrides: Any) -> NewRequirement:
    fields: dict[str, Any] = {
        "project_id": uuid.uuid7(),
        "created_by_user_id": ADA,
        "type": RequirementType.CAPACITY,
        "category": "throughput",
        "title": "API throughput",
        "statement": "The API must support 2,000 requests per second.",
        "priority": RequirementPriority.CRITICAL,
        "status": S.ACTIVE,
        "structured_data": RPS_2K,
    }
    return NewRequirement.create(**(fields | overrides))


def stored(status: S = S.ACTIVE, **overrides: Any) -> Requirement:
    created = new(status=status, **overrides)
    return Requirement(
        id=uuid.uuid7(),
        project_id=created.project_id,
        number=12,
        version=1,
        content=created.content,
        source=created.source,
        confidence=created.confidence,
        created_by_user_id=ADA,
        created_at=NOW,
        updated_at=NOW,
    )


def revise(requirement: Requirement, reason: str | None = "Forecast grew", **changes: Any) -> Requirement:
    revision = requirement.revise(
        expected_version=requirement.version,
        changes=RequirementChanges(**changes),
        change_reason=reason,
        author_user_id=ADA,
    )
    assert revision is not None
    return revision.requirement


# --- creation ------------------------------------------------------------------------------------


def test_create_normalizes_and_parses() -> None:
    created = new(category=" Throughput ", title="  API  throughput ")
    assert (created.content.category, created.content.title) == ("throughput", "API throughput")
    assert isinstance(created.content.constraint, QuantityConstraint)
    assert created.content.structured_data == RPS_2K | {"value": "2000"}
    assert (created.source, created.confidence) == (RequirementSource.USER, None)


def test_requirements_start_as_drafts_by_default() -> None:
    created = NewRequirement.create(
        project_id=uuid.uuid7(),
        created_by_user_id=ADA,
        type=RequirementType.FUNCTIONAL,
        category="order",
        title="Place an order",
        statement="A customer can place an order.",
        priority=RequirementPriority.HIGH,
    )
    assert (created.content.status, created.content.structured_data) == (S.DRAFT, {})


@pytest.mark.parametrize("status", [S.SATISFIED, S.INVALID, S.DEPRECATED])
def test_requirements_cannot_be_created_past_active(status: S) -> None:
    with pytest.raises(InvalidRequirement) as error:
        new(status=status)
    assert error.value.details == {"field": "status", "reason": "not_allowed_at_creation"}


def test_ai_requirements_are_drafts_with_a_confidence() -> None:
    created = new(source=RequirementSource.AI, status=S.DRAFT, confidence=0.7)
    assert created.confidence == Decimal("0.7")
    with pytest.raises(InvalidRequirement, match="invalid") as error:
        new(source=RequirementSource.AI, status=S.ACTIVE, confidence=0.7)
    assert error.value.details["reason"] == "not_allowed_at_creation"
    with pytest.raises(InvalidRequirement) as error:
        new(source=RequirementSource.AI, status=S.DRAFT)
    assert error.value.details == {"field": "confidence", "reason": "required_for_ai"}


def test_people_do_not_state_a_confidence() -> None:
    with pytest.raises(InvalidRequirement) as error:
        new(confidence=1)
    assert error.value.details == {"field": "confidence", "reason": "not_for_user"}


def test_active_measurable_requirements_need_a_constraint_at_creation() -> None:
    with pytest.raises(InvalidRequirement) as error:
        new(structured_data={})
    assert error.value.details == {"field": "structured_data", "reason": "required_when_in_force"}
    assert new(structured_data={}, status=S.DRAFT).content.constraint is None


def test_confidence_is_not_priority() -> None:
    created = new(source=RequirementSource.AI, status=S.DRAFT, confidence="0.7")
    assert (created.content.priority, created.confidence) == (RequirementPriority.CRITICAL, Decimal("0.7"))


# --- revisions -----------------------------------------------------------------------------------


def test_a_material_change_is_a_new_version_and_keeps_identity() -> None:
    requirement = stored()
    revised = revise(requirement, structured_data=RPS_5K, statement="Support 5,000 requests per second.")
    assert (revised.id, revised.number, revised.version) == (requirement.id, 12, 2)
    assert revised.content.structured_data["value"] == "5000"
    assert requirement.version == 1  # the original is untouched
    assert requirement.content.structured_data["value"] == "2000"


def test_the_revision_carries_its_reason_and_author() -> None:
    revision = stored().revise(
        expected_version=1,
        changes=RequirementChanges(structured_data=RPS_5K),
        change_reason="  Traffic forecast increased from 2K to 5K RPS ",
        author_user_id=ADA,
    )
    assert revision is not None
    assert (revision.change_reason, revision.author_user_id) == (
        "Traffic forecast increased from 2K to 5K RPS",
        ADA,
    )


def test_a_stale_version_is_a_conflict() -> None:
    requirement = replace(stored(), version=3)
    with pytest.raises(RequirementVersionConflict) as error:
        requirement.revise(
            expected_version=2, changes=RequirementChanges(title="New"), change_reason="x", author_user_id=ADA
        )
    assert error.value.details == {"current_version": 3}


def test_an_empty_change_is_refused() -> None:
    with pytest.raises(NothingToUpdate):
        stored().revise(
            expected_version=1, changes=RequirementChanges(), change_reason=None, author_user_id=ADA
        )


def test_a_change_to_the_same_values_creates_no_version() -> None:
    requirement = stored()
    same = RequirementChanges(title=" API throughput ", structured_data=RPS_2K, status=S.ACTIVE)
    assert (
        requirement.revise(expected_version=1, changes=same, change_reason=None, author_user_id=ADA) is None
    )


def test_requirements_in_force_need_a_reason_to_change() -> None:
    with pytest.raises(ChangeReasonRequired):
        revise(stored(), reason="  ", title="Throughput")
    assert revise(stored(S.DRAFT), reason=None, title="Throughput").version == 2


def test_status_changes_follow_the_lifecycle() -> None:
    draft = stored(S.DRAFT)
    active = revise(draft, reason=None, status=S.ACTIVE)
    satisfied = revise(active, status=S.SATISFIED)
    assert (active.content.status, satisfied.content.status, satisfied.version) == (S.ACTIVE, S.SATISFIED, 3)
    with pytest.raises(InvalidStatusTransition):
        revise(draft, reason=None, status=S.SATISFIED)


def test_promoting_a_draft_validates_it_in_full() -> None:
    draft = stored(S.DRAFT, structured_data={})
    with pytest.raises(InvalidRequirement) as error:
        revise(draft, reason=None, status=S.ACTIVE)
    assert error.value.details["reason"] == "required_when_in_force"
    assert revise(draft, reason=None, status=S.ACTIVE, structured_data=RPS_2K).content.status is S.ACTIVE


def test_removing_the_constraint_of_an_active_requirement_is_refused() -> None:
    with pytest.raises(InvalidRequirement):
        revise(stored(), structured_data={})


def test_satisfied_requirements_are_reopened_before_editing() -> None:
    satisfied = revise(stored(), status=S.SATISFIED)
    with pytest.raises(RequirementLocked) as error:
        revise(satisfied, structured_data=RPS_5K)
    assert error.value.details == {"status": "satisfied"}
    reopened = revise(satisfied, structured_data=RPS_5K, status=S.ACTIVE)
    assert (reopened.content.status, reopened.content.structured_data["value"]) == (S.ACTIVE, "5000")


def test_deprecated_requirements_never_change() -> None:
    deprecated = revise(stored(), status=S.DEPRECATED)
    with pytest.raises(RequirementLocked):
        revise(deprecated, title="Back")
    with pytest.raises(InvalidStatusTransition):
        revise(deprecated, status=S.ACTIVE)


def test_invalid_requirements_go_back_to_draft_for_rework() -> None:
    invalid = revise(stored(), status=S.INVALID)
    reworked = revise(invalid, reason=None, status=S.DRAFT, title="Throughput, reworked")
    assert (reworked.content.status, reworked.content.title) == (S.DRAFT, "Throughput, reworked")


def test_a_revision_is_validated_like_a_creation() -> None:
    with pytest.raises(InvalidRequirement) as error:
        revise(stored(), structured_data=RPS_2K | {"value": -50})
    assert error.value.details == {"field": "structured_data.value", "reason": "out_of_range"}
    with pytest.raises(InvalidRequirement):
        revise(stored(), category="latency")  # rps is not a latency metric


def test_fixed_fields_cannot_be_revised() -> None:
    fields = set(RequirementChanges.__dataclass_fields__)
    assert fields.isdisjoint({"type", "source", "confidence", "project_id", "number", "created_by_user_id"})


# --- deletion ------------------------------------------------------------------------------------


def test_deletion_is_soft_and_final() -> None:
    deleted = stored().delete(NOW)
    assert deleted.is_deleted
    with pytest.raises(RequirementNotFound):
        deleted.delete(NOW)
    with pytest.raises(RequirementNotFound):
        revise(deleted, title="Ghost")


def test_reference() -> None:
    assert stored().reference == "REQ-12"


def test_stored_content_loads_without_revalidation() -> None:
    """History must stay readable even if the rules tighten after it was written."""
    legacy = RequirementContent(
        type=RequirementType.CAPACITY,
        category="legacy_category",
        title="Old",
        statement="Written under older rules.",
        priority=RequirementPriority.LOW,
        status=S.ACTIVE,
        constraint=None,
    )
    assert legacy.structured_data == {}
    with pytest.raises(InvalidRequirement):
        legacy.validated()
