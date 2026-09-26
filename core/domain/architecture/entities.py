"""A project's architecture: its identity, its current revision and its layout.

One architecture per project. Its content lives in revisions (see versions.py): the architecture
row only says which revision is current. The layout (where each node is drawn) is presentation,
stored beside the architecture and never part of a revision.
"""

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from core.architecture_ir.model import MAX_NODES
from core.architecture_ir.values import ELEMENT_ID

from .errors import InvalidLayout
from .versions import RevisionSource

MAX_COORDINATE = 1_000_000.0


@dataclass(frozen=True, slots=True)
class NewArchitecture:
    id: uuid.UUID
    project_id: uuid.UUID
    created_by_user_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class Architecture:
    id: uuid.UUID
    project_id: uuid.UUID
    current_revision: int
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RevisionSummary:
    """A revision without its content, for history listings."""

    number: int
    parent_number: int | None
    source: RevisionSource
    summary: str
    reason: str | None
    content_hash: str
    ir_schema_version: int
    requirement_set_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Position:
    x: float
    y: float

    def __post_init__(self) -> None:
        for value in (self.x, self.y):
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
                raise InvalidLayout(details={"reason": "invalid_position"})
            if abs(value) > MAX_COORDINATE:
                raise InvalidLayout(details={"reason": "position_out_of_range"})


@dataclass(frozen=True, slots=True)
class ArchitectureLayout:
    positions: Mapping[str, Position] = field(default_factory=dict)
    updated_by_user_id: uuid.UUID | None = None
    updated_at: datetime | None = None


def check_positions(positions: Mapping[str, Position], node_ids: frozenset[str]) -> None:
    """Positions are for nodes of the architecture, and there are not more of them than nodes."""
    if len(positions) > MAX_NODES:
        raise InvalidLayout(details={"reason": "too_many"})
    for node_id in positions:
        if not isinstance(node_id, str) or not ELEMENT_ID.fullmatch(node_id):
            raise InvalidLayout(details={"reason": "invalid_node_id"})
        if node_id not in node_ids:
            raise InvalidLayout(
                f"There is no node {node_id!r} in the current revision.",
                details={"reason": "unknown_node", "node_id": node_id},
            )
