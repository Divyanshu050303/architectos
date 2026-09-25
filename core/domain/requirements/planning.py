"""The Architecture Planning Input: the stable contract between requirements and the architecture
engines. Requirements -> Requirement Set -> Architecture Planning Input -> Architecture IR.

It depends on nothing outside the requirements and projects domains (no HTTP, SQL, LLM or cloud
resources), and it is versioned: a change to its shape or meaning is a new SCHEMA_VERSION, never
an edit of version 1. Engines read only this, never requirements directly.

Schema version 1 (JSON; snake_case keys; numbers as exact decimal strings):

    {
      "schema_version": 1,
      "project": {"id": "<uuid>", "settings": {"cloud_provider": "aws" | ... | null, "currency": "USD"}},
      "requirements": [                    # ordered by requirement number
        {
          "id": "<uuid>", "reference": "REQ-3", "version": 2,
          "type": "capacity", "category": "throughput", "priority": "critical", "status": "active",
          "title": "...", "statement": "...",
          "source": "user" | "ai", "confidence": "0.9" | null,
          "constraint": null
            | {"metric", "operator", "value", "unit", "percentile"?}   # canonical unit
            | {"metric", "operator": "in", "values": [...]}             # sorted
        }
      ]
    }

The content hash is SHA-256 over the canonical JSON encoding (sorted keys, no whitespace, UTF-8):
the same requirements and settings always produce the same hash, whatever set they are in.
"""

import hashlib
import json
from typing import Any, Final, Literal, NotRequired, TypedDict, cast

from core.domain.projects.entities import Project

from .entities import Requirement
from .normalization import canonical_data
from .value_objects import decimal_to_str

SCHEMA_VERSION: Final[Literal[1]] = 1


class PlanningConstraintV1(TypedDict):
    metric: str
    operator: str
    value: NotRequired[str]
    unit: NotRequired[str]
    percentile: NotRequired[str]
    values: NotRequired[list[str]]


class PlanningRequirementV1(TypedDict):
    id: str
    reference: str
    version: int
    type: str
    category: str
    priority: str
    status: str
    title: str
    statement: str
    source: str
    confidence: str | None
    constraint: PlanningConstraintV1 | None


class PlanningProjectV1(TypedDict):
    id: str
    settings: dict[str, Any]


class PlanningInputV1(TypedDict):
    schema_version: Literal[1]
    project: PlanningProjectV1
    requirements: list[PlanningRequirementV1]


def _requirement(requirement: Requirement) -> PlanningRequirementV1:
    content = requirement.content
    constraint = canonical_data(content.constraint)
    return {
        "id": str(requirement.id),
        "reference": requirement.reference,
        "version": requirement.version,
        "type": content.type.value,
        "category": content.category,
        "priority": content.priority.value,
        "status": content.status.value,
        "title": content.title,
        "statement": content.statement,
        "source": requirement.source.value,
        "confidence": decimal_to_str(requirement.confidence) if requirement.confidence is not None else None,
        "constraint": cast(PlanningConstraintV1, constraint) if constraint is not None else None,
    }


def build_planning_input(project: Project, requirements: list[Requirement]) -> PlanningInputV1:
    """Deterministic: the same project settings and requirement versions give the same input."""
    return {
        "schema_version": SCHEMA_VERSION,
        "project": {"id": str(project.id), "settings": project.settings.to_dict()},
        "requirements": [_requirement(r) for r in sorted(requirements, key=lambda r: r.number)],
    }


def canonical_json(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def content_hash(document: object) -> str:
    return hashlib.sha256(canonical_json(document)).hexdigest()
