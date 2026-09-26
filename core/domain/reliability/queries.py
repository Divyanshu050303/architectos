"""Listing reliability analyses, and an analysis's components and findings."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from core.domain import pagination
from core.domain.capacity.results import Certainty, ComponentStatus
from core.domain.validation.results import Severity

from .results import FindingType

_ANALYSES = "reliability_analyses"
_COMPONENTS = "reliability_components"
_FINDINGS = "reliability_findings"


@dataclass(frozen=True, slots=True)
class ReliabilityAnalysisQuery:
    """An architecture's analyses, newest first."""

    revision: int | None = None
    after: tuple[datetime, uuid.UUID] | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ReliabilityComponentQuery:
    status: ComponentStatus | None = None
    after: str | None = None  # the last node id of the previous page
    limit: int = 100


@dataclass(frozen=True, slots=True)
class ReliabilityFindingQuery:
    """An analysis's findings in their canonical order (most severe first)."""

    severity: Severity | None = None
    type: FindingType | None = None
    certainty: Certainty | None = None
    after: int | None = None  # the last position of the previous page
    limit: int = 100


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


def encode_component_cursor(node_id: str) -> str:
    return pagination.encode_cursor([_COMPONENTS, node_id])


def decode_component_cursor(raw: str) -> str:
    kind, node_id = pagination.decode_cursor(raw, length=2)
    if kind != _COMPONENTS or not node_id:
        raise pagination.InvalidCursor
    return node_id


def encode_finding_cursor(position: int) -> str:
    return pagination.encode_cursor([_FINDINGS, str(position)])


def decode_finding_cursor(raw: str) -> int:
    kind, position = pagination.decode_cursor(raw, length=2)
    if kind != _FINDINGS or not position.isdigit():
        raise pagination.InvalidCursor
    return int(position)
