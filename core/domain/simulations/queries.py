"""Listing simulations, and a simulation's component outcomes and deltas. Cursors carry their own
kinds, so a cursor of another listing is refused."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from core.domain import pagination

from .values import AnalysisKind, Impact

_SIMULATIONS = "simulations"
_COMPONENTS = "simulation_components"
_DELTAS = "simulation_deltas"


@dataclass(frozen=True, slots=True)
class SimulationQuery:
    """An architecture's simulations, newest first."""

    revision: int | None = None
    after: tuple[datetime, uuid.UUID] | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class SimulationComponentQuery:
    unavailable: bool | None = None
    impact: Impact | None = None
    after: str | None = None  # the last node id of the previous page
    limit: int = 100


@dataclass(frozen=True, slots=True)
class SimulationDeltaQuery:
    """A simulation's deltas in their canonical order (analysis, element, metric)."""

    analysis: AnalysisKind | None = None
    element_id: str | None = None
    comparable: bool | None = None
    after: int | None = None  # the last position of the previous page
    limit: int = 100


def encode_simulation_cursor(requested_at: datetime, simulation_id: uuid.UUID) -> str:
    return pagination.encode_cursor([_SIMULATIONS, requested_at.isoformat(), str(simulation_id)])


def decode_simulation_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    kind, requested_at, simulation_id = pagination.decode_cursor(raw, length=3)
    if kind != _SIMULATIONS:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(requested_at), uuid.UUID(simulation_id)
    except ValueError:
        raise pagination.InvalidCursor from None


def encode_component_cursor(node_id: str) -> str:
    return pagination.encode_cursor([_COMPONENTS, node_id])


def decode_component_cursor(raw: str) -> str:
    kind, node_id = pagination.decode_cursor(raw, length=2)
    if kind != _COMPONENTS or not node_id:
        raise pagination.InvalidCursor
    return node_id


def encode_delta_cursor(position: int) -> str:
    return pagination.encode_cursor([_DELTAS, str(position)])


def decode_delta_cursor(raw: str) -> int:
    kind, position = pagination.decode_cursor(raw, length=2)
    if kind != _DELTAS or not position.isdigit():
        raise pagination.InvalidCursor
    return int(position)
