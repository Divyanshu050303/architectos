"""Requirement analyses: what a person typed, and what the Requirements Engine made of it.

An analysis record is append-only and keeps the raw input **exactly as written** (never the
normalized text), its SHA-256, the version of the engine that analyzed it and the result. It is
what makes an extracted requirement explainable later: a promoted requirement's ``origin`` points
at the analysis and at the candidate it was promoted from, and so at the exact span of the user's
own words.
"""

import hashlib
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .errors import InvalidRequirementInput

MAX_INPUT_CHARACTERS = 20_000
_ALLOWED_CONTROLS = frozenset({"\n", "\r", "\t"})


def validate_raw_input(raw: str) -> str:
    """The input as given (not trimmed, not normalized), if it can be stored and analyzed."""
    if not raw.strip():
        raise InvalidRequirementInput(details={"reason": "empty"})
    if len(raw) > MAX_INPUT_CHARACTERS:
        raise InvalidRequirementInput(details={"reason": "too_long", "max_characters": MAX_INPUT_CHARACTERS})
    if any(unicodedata.category(c) == "Cc" and c not in _ALLOWED_CONTROLS for c in raw):
        raise InvalidRequirementInput(details={"reason": "control_characters"})
    return raw


def input_sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Origin:
    """Where a requirement was extracted from."""

    analysis_id: uuid.UUID
    candidate_key: str


@dataclass(frozen=True, slots=True)
class NewRequirementAnalysis:
    project_id: uuid.UUID
    raw_input: str
    engine_version: str
    result: dict[str, Any]
    created_by_user_id: uuid.UUID

    @property
    def input_sha256(self) -> str:
        return input_sha256(self.raw_input)


@dataclass(frozen=True, slots=True)
class RequirementAnalysis:
    id: uuid.UUID
    project_id: uuid.UUID
    raw_input: str
    input_sha256: str
    engine_version: str
    result: dict[str, Any]
    created_by_user_id: uuid.UUID | None
    created_at: datetime
