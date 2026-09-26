"""Listing runs and a run's findings: filters, keyset positions and page sizes."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from core.domain import pagination

from .results import Category, Severity

_RUNS = "validation_runs"
_FINDINGS = "validation_findings"


@dataclass(frozen=True, slots=True)
class RunQuery:
    """A architecture's runs, newest first."""

    revision: int | None = None
    after: tuple[datetime, uuid.UUID] | None = None  # the last (requested_at, id) of the previous page
    limit: int = 50


@dataclass(frozen=True, slots=True)
class FindingQuery:
    """A run's findings in their canonical order (most severe first), optionally filtered."""

    severity: Severity | None = None
    category: Category | None = None
    blocking: bool | None = None
    rule_id: str | None = None
    entity_id: str | None = None
    after: int | None = None  # the last position of the previous page
    limit: int = 100


def encode_run_cursor(requested_at: datetime, run_id: uuid.UUID) -> str:
    return pagination.encode_cursor([_RUNS, requested_at.isoformat(), str(run_id)])


def decode_run_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    kind, requested_at, run_id = pagination.decode_cursor(raw, length=3)
    if kind != _RUNS:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(requested_at), uuid.UUID(run_id)
    except ValueError:
        raise pagination.InvalidCursor from None


def encode_finding_cursor(position: int) -> str:
    return pagination.encode_cursor([_FINDINGS, str(position)])


def decode_finding_cursor(raw: str) -> int:
    kind, position = pagination.decode_cursor(raw, length=2)
    if kind != _FINDINGS or not position.isdigit():
        raise pagination.InvalidCursor
    return int(position)
