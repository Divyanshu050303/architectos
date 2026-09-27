"""Listing evolution analyses and their candidates, and decisions. Cursors carry their own kinds, so a
cursor of another listing is refused."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from core.domain import pagination

from .values import CandidateCategory, ValidationState

_ANALYSES = "evolution_analyses"
_CANDIDATES = "evolution_candidates"
_DECISIONS = "decisions"


@dataclass(frozen=True, slots=True)
class EvolutionQuery:
    """An architecture's evolution analyses, newest first."""

    revision: int | None = None
    after: tuple[datetime, uuid.UUID] | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class CandidateQuery:
    """An analysis's candidates in their canonical order (category, then id) — not a ranking."""

    category: CandidateCategory | None = None
    validation: ValidationState | None = None
    goal: str | None = None  # a goal key
    after: int | None = None  # the last position of the previous page
    limit: int = 50


def encode_analysis_cursor(requested_at: datetime, analysis_id: uuid.UUID) -> str:
    return pagination.encode_cursor([_ANALYSES, requested_at.isoformat(), str(analysis_id)])


def decode_analysis_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    kind, requested_at, analysis_id = pagination.decode_cursor(raw, length=3)
    if kind != _ANALYSES:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(requested_at), uuid.UUID(analysis_id)
    except ValueError:
        raise pagination.InvalidCursor from None


def _position_cursor(kind: str, position: int) -> str:
    return pagination.encode_cursor([kind, str(position)])


def _decode_position(raw: str, expected: str) -> int:
    kind, position = pagination.decode_cursor(raw, length=2)
    if kind != expected or not position.isdigit():
        raise pagination.InvalidCursor
    return int(position)


def encode_candidate_cursor(position: int) -> str:
    return _position_cursor(_CANDIDATES, position)


def decode_candidate_cursor(raw: str) -> int:
    return _decode_position(raw, _CANDIDATES)


def encode_decision_cursor(number: int) -> str:
    return _position_cursor(_DECISIONS, number)


def decode_decision_cursor(raw: str) -> int:
    return _decode_position(raw, _DECISIONS)
