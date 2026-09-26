"""Listing analyses, and an analysis's components and bottlenecks."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from core.domain import pagination

from .results import Certainty, ComponentStatus

_ANALYSES = "capacity_analyses"
_COMPONENTS = "capacity_components"


@dataclass(frozen=True, slots=True)
class AnalysisQuery:
    """An architecture's analyses, newest first."""

    revision: int | None = None
    after: tuple[datetime, uuid.UUID] | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ComponentQuery:
    """An analysis's components by node id."""

    status: ComponentStatus | None = None
    after: str | None = None  # the last node id of the previous page
    limit: int = 100


@dataclass(frozen=True, slots=True)
class BottleneckQuery:
    certainty: Certainty | None = None


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
