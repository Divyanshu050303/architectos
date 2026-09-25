"""Requirement sets: immutable, numbered snapshots of a project's requirements (v1, v2, ...).

A set records exactly which requirement versions an architecture is planned or evaluated
against, plus the Architecture Planning Input built from them, so that "Architecture v7 was
evaluated against Requirement Set v3" stays explainable forever: later requirement changes create
new versions and never touch what a set pinned.

Rules:
- only requirements in force (active or satisfied) can be pinned: drafts are not authoritative;
- every pinned requirement must pass today's validation;
- a set with mathematically conflicting requirements is refused (no architecture can satisfy it);
- a set is never empty and never changes.
"""

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.domain.text import has_forbidden_characters

from .errors import InvalidRequirementSet

MAX_SET_REQUIREMENTS = 1000
MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 2000


def normalize_set_name(raw: str) -> str:
    name = unicodedata.normalize("NFC", " ".join(raw.split()))
    if len(name) > MAX_NAME_LENGTH or has_forbidden_characters(name):
        raise InvalidRequirementSet(details={"field": "name", "reason": "invalid"})
    return name


def normalize_set_description(raw: str) -> str:
    description = unicodedata.normalize("NFC", raw.replace("\r\n", "\n").strip())
    if len(description) > MAX_DESCRIPTION_LENGTH or has_forbidden_characters(description, {"\n", "\t"}):
        raise InvalidRequirementSet(details={"field": "description", "reason": "invalid"})
    return description


@dataclass(frozen=True, slots=True)
class PinnedVersion:
    requirement_id: uuid.UUID
    number: int
    version: int

    @property
    def reference(self) -> str:
        return f"REQ-{self.number}"


@dataclass(frozen=True, slots=True)
class NewRequirementSet:
    project_id: uuid.UUID
    name: str
    description: str
    items: tuple[PinnedVersion, ...]
    schema_version: int
    planning_input: dict[str, Any]
    content_hash: str
    created_by_user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class RequirementSet:
    id: uuid.UUID
    project_id: uuid.UUID
    number: int
    name: str
    description: str
    schema_version: int
    content_hash: str
    requirement_count: int
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    # Empty in listings; filled when a single set is loaded.
    items: tuple[PinnedVersion, ...] = ()

    @property
    def label(self) -> str:
        return f"v{self.number}"
