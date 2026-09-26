import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from core.domain.requirements.candidates import ExtractionMethod, RequirementCandidate, SourceSpan
from core.domain.requirements.entities import RequirementContent
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementScope,
    RequirementSource,
    RequirementStatus,
    RequirementType,
)
from core.domain.requirements.errors import InvalidRequirement
from core.domain.requirements.value_objects import parse_structured_data

RAW = "Our API should support 2k requests/sec at peak."
SPAN = SourceSpan(start=RAW.index("2k"), end=RAW.index(" at"), text="2k requests/sec")


def content(**overrides: object) -> RequirementContent:
    fields: dict[str, object] = {
        "type": RequirementType.CAPACITY,
        "category": "throughput",
        "title": "Throughput",
        "statement": RAW,
        "priority": RequirementPriority.MEDIUM,
        "status": RequirementStatus.DRAFT,
        "constraint": parse_structured_data(
            {"metric": "requests_per_second", "operator": ">=", "value": "2000", "unit": "requests/second"}
        ),
        "scope": RequirementScope.API,
    }
    return RequirementContent(**(fields | overrides))  # type: ignore[arg-type]


def candidate(**overrides: object) -> RequirementCandidate:
    fields: dict[str, object] = {
        "method": ExtractionMethod.PATTERN,
        "content": content(),
        "confidence": Decimal("0.95"),
        "span": SPAN,
    }
    return RequirementCandidate(**(fields | overrides))  # type: ignore[arg-type]


def test_a_span_points_at_the_exact_text() -> None:
    assert SPAN.matches(RAW)
    assert not SPAN.matches(RAW.upper())
    for start, end, text in ((-1, 2, "ab"), (3, 3, ""), (0, 2, "abc")):
        with pytest.raises(ValueError, match="span"):
            SourceSpan(start, end, text)


def test_the_extraction_method_decides_the_source() -> None:
    assert candidate().source is RequirementSource.SYSTEM
    assert candidate(method=ExtractionMethod.LLM).source is RequirementSource.AI


def test_keys_are_deterministic_and_identify_the_interpretation() -> None:
    assert candidate().key == candidate().key
    assert candidate().key.startswith("cand_")
    assert len(candidate().key) == len("cand_") + 16
    # The wording of the title or a confidence does not change what was interpreted...
    assert candidate(content=content(title="Other"), confidence=Decimal("0.5")).key == candidate().key
    # ...but a different span, scope, value or method does.
    assert candidate(span=None).key != candidate().key
    assert candidate(content=content(scope=RequirementScope.SYSTEM)).key != candidate().key
    assert candidate(method=ExtractionMethod.LLM).key != candidate().key


def test_candidates_are_always_drafts_with_a_valid_confidence() -> None:
    with pytest.raises(ValueError, match="drafts"):
        candidate(content=content(status=RequirementStatus.ACTIVE))
    with pytest.raises(InvalidRequirement):
        candidate(confidence=Decimal("1.5"))


def test_an_invalid_interpretation_is_reported_not_promoted() -> None:
    wrong = candidate(
        content=content(type=RequirementType.PERFORMANCE, category="latency")
    )  # rps: no latency
    problem = wrong.problem()
    assert problem is not None
    assert problem.details == {"field": "structured_data.metric", "reason": "not_allowed_for_category"}
    with pytest.raises(InvalidRequirement):
        wrong.to_new_requirement(project_id=uuid.uuid7(), created_by_user_id=uuid.uuid7())
    assert candidate().problem() is None


def test_promotion_creates_a_draft_that_remembers_its_source_and_confidence() -> None:
    created = candidate(method=ExtractionMethod.LLM, confidence=Decimal("0.62")).to_new_requirement(
        project_id=uuid.uuid7(), created_by_user_id=uuid.uuid7()
    )
    assert (created.content.status, created.source, created.confidence) == (
        RequirementStatus.DRAFT,
        RequirementSource.AI,
        Decimal("0.62"),
    )
    assert created.content.scope is RequirementScope.API
    assert created.content.constraint == candidate().content.constraint


def test_promotion_revalidates_like_any_creation() -> None:
    blank_title = candidate(content=replace(content(), title="   "))
    with pytest.raises(InvalidRequirement) as error:
        blank_title.to_new_requirement(project_id=uuid.uuid7(), created_by_user_id=uuid.uuid7())
    assert error.value.details == {"field": "title", "reason": "length"}
