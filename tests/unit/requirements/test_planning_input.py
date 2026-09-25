import json
import uuid
from datetime import UTC, datetime
from typing import Any

from core.domain.projects.entities import Project
from core.domain.projects.enums import ProjectStatus
from core.domain.projects.value_objects import CloudProvider, ProjectSettings
from core.domain.requirements.entities import NewRequirement, Requirement
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementSource,
    RequirementStatus,
    RequirementType,
)
from core.domain.requirements.planning import (
    SCHEMA_VERSION,
    build_planning_input,
    canonical_json,
    content_hash,
)

NOW = datetime(2026, 9, 25, tzinfo=UTC)
PROJECT = Project(
    id=uuid.UUID("01900000-0000-7000-8000-000000000001"),
    organization_id=uuid.uuid7(),
    name="Food Delivery",
    slug="food-delivery",
    description="",
    status=ProjectStatus.ACTIVE,
    settings=ProjectSettings(cloud_provider=CloudProvider.AWS, currency="EUR"),
    created_by_user_id=None,
    archived_at=None,
    deleted_at=None,
    created_at=NOW,
    updated_at=NOW,
)


def requirement(number: int, version: int = 1, **overrides: Any) -> Requirement:
    fields: dict[str, Any] = {
        "project_id": PROJECT.id,
        "created_by_user_id": uuid.uuid7(),
        "type": RequirementType.PERFORMANCE,
        "category": "latency",
        "title": "Checkout latency",
        "statement": "Checkout p95 latency stays under 1.5 s.",
        "priority": RequirementPriority.CRITICAL,
        "status": RequirementStatus.ACTIVE,
        "structured_data": {
            "metric": "latency",
            "operator": "<=",
            "value": "1.5",
            "unit": "s",
            "percentile": "p95",
        },
    }
    created = NewRequirement.create(**(fields | overrides))
    return Requirement(
        id=uuid.UUID(int=number),
        project_id=PROJECT.id,
        number=number,
        version=version,
        content=created.content,
        source=created.source,
        confidence=created.confidence,
        created_by_user_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def test_the_contract_shape() -> None:
    document = build_planning_input(PROJECT, [requirement(3, version=2)])
    assert document == {
        "schema_version": SCHEMA_VERSION,
        "project": {"id": str(PROJECT.id), "settings": {"cloud_provider": "aws", "currency": "EUR"}},
        "requirements": [
            {
                "id": str(uuid.UUID(int=3)),
                "reference": "REQ-3",
                "version": 2,
                "type": "performance",
                "category": "latency",
                "priority": "critical",
                "status": "active",
                "title": "Checkout latency",
                "statement": "Checkout p95 latency stays under 1.5 s.",
                "scope": "system",
                "source": "user",
                "confidence": None,
                "origin": None,
                # canonical unit: 1.5 s -> 1500 ms
                "constraint": {
                    "metric": "latency",
                    "operator": "<=",
                    "value": "1500",
                    "unit": "ms",
                    "percentile": "95",
                },
            }
        ],
    }


def test_qualitative_and_ai_requirements() -> None:
    document = build_planning_input(
        PROJECT,
        [
            requirement(
                1,
                type=RequirementType.SECURITY,
                category="encryption",
                status=RequirementStatus.DRAFT,
                source=RequirementSource.AI,
                confidence="0.9",
                structured_data={},
            )
        ],
    )
    [item] = document["requirements"]
    assert (item["constraint"], item["source"], item["confidence"]) == (None, "ai", "0.9")


def test_order_does_not_matter_and_the_hash_is_stable() -> None:
    a, b = requirement(1), requirement(2, title="Search latency")
    forward = build_planning_input(PROJECT, [a, b])
    backward = build_planning_input(PROJECT, [b, a])
    assert forward == backward
    assert content_hash(forward) == content_hash(backward)
    assert [r["reference"] for r in forward["requirements"]] == ["REQ-1", "REQ-2"]


def test_any_change_changes_the_hash() -> None:
    base = content_hash(build_planning_input(PROJECT, [requirement(1)]))
    assert content_hash(build_planning_input(PROJECT, [requirement(1, version=2)])) != base
    tighter = requirement(
        1,
        structured_data={
            "metric": "latency",
            "operator": "<=",
            "value": "1",
            "unit": "s",
            "percentile": "p95",
        },
    )
    assert content_hash(build_planning_input(PROJECT, [tighter])) != base


def test_equal_meaning_in_other_units_hashes_equally() -> None:
    seconds = requirement(1)
    millis = requirement(
        1,
        structured_data={
            "metric": "latency",
            "operator": "<=",
            "value": "1500",
            "unit": "ms",
            "percentile": 95,
        },
    )
    assert content_hash(build_planning_input(PROJECT, [seconds])) == content_hash(
        build_planning_input(PROJECT, [millis])
    )


def test_the_hash_survives_a_json_round_trip() -> None:
    """What is stored (JSONB) and read back must hash to the stored hash."""
    document = build_planning_input(PROJECT, [requirement(1), requirement(2, title="Ünïcode ✓")])
    reloaded = json.loads(json.dumps(document, indent=2))
    assert content_hash(reloaded) == content_hash(document)
    assert canonical_json(document).decode() == json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert len(content_hash(document)) == 64
