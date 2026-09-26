"""The Architecture Planning Input: the stable contract between requirements and the architecture
engines. Requirements -> Requirement Set -> Architecture Planning Input -> Architecture IR.

It depends on nothing outside the requirements and projects domains (no HTTP, SQL, LLM or cloud
resources), and it is versioned: a change to its shape or meaning is a new SCHEMA_VERSION, never
an edit of an old one. Engines read only this, never requirements directly. Documents are stored
with their requirement set, so every version ever written stays readable exactly as it was.

Schema version 2 (current; JSON; snake_case keys; numbers as exact decimal strings):

    {
      "schema_version": 2,
      "project": {"id": "<uuid>", "settings": {"cloud_provider": "aws" | ... | null, "currency": "USD"}},
      "requirements": [                    # ordered by requirement number
        {
          "id": "<uuid>", "reference": "REQ-3", "version": 2,
          "type": "capacity", "category": "throughput", "priority": "critical", "status": "active",
          "title": "...", "statement": "...",
          "scope": "system" | "api" | ...,       # "system" also when unspecified
          "source": "user" | "ai" | "imported" | "discovery" | "system", "confidence": "0.9" | null,
          "origin": null | {"analysis_id": "<uuid>", "candidate_key": "..."},  # where it was extracted
          "constraint": null
            | {"metric", "operator": ">=" | ">" | "<=" | "<" | "==", "value", "unit", "percentile"?}
            | {"metric", "operator": "between", "min", "max", "unit", "percentile"?}   # inclusive
            | {"metric", "operator": "in", "values": [...]}                            # sorted
        }                                                  # quantities in the canonical unit
      ]
    }

Schema version 1 (requirement sets created before 2026-09-26) is the same without ``scope`` and
``origin``, with sources ``user`` | ``ai`` and without ``==`` or ``between``.

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

SCHEMA_VERSION: Final[Literal[2]] = 2


class PlanningConstraintV2(TypedDict):
    metric: str
    operator: str
    value: NotRequired[str]
    min: NotRequired[str]
    max: NotRequired[str]
    unit: NotRequired[str]
    percentile: NotRequired[str]
    values: NotRequired[list[str]]


class PlanningOriginV2(TypedDict):
    analysis_id: str
    candidate_key: str


class PlanningRequirementV2(TypedDict):
    id: str
    reference: str
    version: int
    type: str
    category: str
    priority: str
    status: str
    title: str
    statement: str
    scope: str
    source: str
    confidence: str | None
    origin: PlanningOriginV2 | None
    constraint: PlanningConstraintV2 | None


class PlanningProjectV2(TypedDict):
    id: str
    settings: dict[str, Any]


class PlanningInputV2(TypedDict):
    schema_version: Literal[2]
    project: PlanningProjectV2
    requirements: list[PlanningRequirementV2]


def _requirement(requirement: Requirement) -> PlanningRequirementV2:
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
        "scope": content.scope.value,
        "source": requirement.source.value,
        "confidence": decimal_to_str(requirement.confidence) if requirement.confidence is not None else None,
        "origin": {
            "analysis_id": str(requirement.origin.analysis_id),
            "candidate_key": requirement.origin.candidate_key,
        }
        if requirement.origin
        else None,
        "constraint": cast(PlanningConstraintV2, constraint) if constraint is not None else None,
    }


def build_planning_input(project: Project, requirements: list[Requirement]) -> PlanningInputV2:
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
