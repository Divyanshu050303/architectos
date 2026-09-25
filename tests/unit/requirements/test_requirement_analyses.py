import uuid

import pytest

from core.domain.requirements.analyses import (
    MAX_INPUT_CHARACTERS,
    NewRequirementAnalysis,
    Origin,
    input_sha256,
    validate_raw_input,
)
from core.domain.requirements.entities import Requirement
from core.domain.requirements.errors import InvalidRequirementInput
from core.domain.requirements.planning import build_planning_input
from engines.requirements.extractor import extract

from .test_planning_input import NOW, PROJECT


def test_raw_input_is_kept_exactly_as_written() -> None:
    raw = "  Support 2k RPS.\r\n\tAnd 99.9% availability.  "
    assert validate_raw_input(raw) == raw


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "empty"),
        ("   \n\t ", "empty"),
        ("x" * (MAX_INPUT_CHARACTERS + 1), "too_long"),
        ("a\x00b", "control_characters"),
    ],
)
def test_unusable_input_is_refused(raw: str, reason: str) -> None:
    with pytest.raises(InvalidRequirementInput) as error:
        validate_raw_input(raw)
    assert error.value.details["reason"] == reason


def test_the_longest_accepted_input() -> None:
    assert validate_raw_input("x" * MAX_INPUT_CHARACTERS)


def test_the_input_hash_is_of_the_exact_text() -> None:
    analysis = NewRequirementAnalysis(uuid.uuid7(), "Support 2k RPS.", "rules-1.0.0", {}, uuid.uuid7())
    assert analysis.input_sha256 == input_sha256("Support 2k RPS.")
    assert input_sha256("Support 2k RPS.") != input_sha256("Support 2k RPS. ")
    assert len(analysis.input_sha256) == 64


def test_a_promoted_candidate_remembers_its_origin_and_the_planning_input_carries_it() -> None:
    analysis_id = uuid.uuid7()
    [candidate] = extract("Support at least 2000 rps.").candidates
    created = candidate.to_new_requirement(
        project_id=PROJECT.id, created_by_user_id=uuid.uuid7(), analysis_id=analysis_id
    )
    assert created.origin == Origin(analysis_id, candidate.key)
    requirement = Requirement(
        id=uuid.uuid7(),
        project_id=PROJECT.id,
        number=1,
        version=1,
        content=created.content,
        source=created.source,
        confidence=created.confidence,
        created_by_user_id=None,
        created_at=NOW,
        updated_at=NOW,
        origin=created.origin,
    )
    [item] = build_planning_input(PROJECT, [requirement])["requirements"]
    assert item["origin"] == {"analysis_id": str(analysis_id), "candidate_key": candidate.key}


def test_without_an_analysis_there_is_no_origin() -> None:
    [candidate] = extract("Support at least 2000 rps.").candidates
    assert (
        candidate.to_new_requirement(project_id=uuid.uuid7(), created_by_user_id=uuid.uuid7()).origin is None
    )
