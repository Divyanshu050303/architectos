"""An architecture of a project: its identity, metadata, lifecycle, current revision and layout.

A project may have many architectures (alternatives, stages, discovered estates). Each has:

- **metadata** (name, description) owned by this record: changing it is audited but creates no
  revision. The IR snapshot of each revision keeps its own ``name``/``description``: the design's
  title as of that revision.
- **content**, which lives only in immutable revisions (see versions.py); the record points at the
  current one.
- a **lifecycle** like projects: active → archived (read-only; can be restored) → deleted (soft,
  only from archived; hidden everywhere; revisions are kept, nothing is purged).
- a **layout** (where nodes are drawn), presentation only, never part of a revision.
"""

import math
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from core.architecture_ir.model import MAX_NODES
from core.architecture_ir.values import ELEMENT_ID
from core.domain.text import has_forbidden_characters

from .errors import (
    ArchitectureArchived,
    ArchitectureNotArchived,
    ArchitectureNotFound,
    InvalidArchitectureMetadata,
    InvalidLayout,
)
from .versions import RevisionSource

MAX_COORDINATE = 1_000_000.0
MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 2000


class ArchitectureStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"  # read-only: only restore (or, from here, delete) is allowed


def normalize_architecture_name(raw: str) -> str:
    name = unicodedata.normalize("NFC", " ".join(raw.split()))
    if not name or len(name) > MAX_NAME_LENGTH:
        raise InvalidArchitectureMetadata(details={"field": "name", "reason": "length"})
    if has_forbidden_characters(name):
        raise InvalidArchitectureMetadata(details={"field": "name", "reason": "control_characters"})
    return name


def normalize_architecture_description(raw: str) -> str:
    description = unicodedata.normalize("NFC", raw.replace("\r\n", "\n").strip())
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise InvalidArchitectureMetadata(details={"field": "description", "reason": "length"})
    if has_forbidden_characters(description, frozenset({"\n", "\t"})):
        raise InvalidArchitectureMetadata(details={"field": "description", "reason": "control_characters"})
    return description


@dataclass(frozen=True, slots=True)
class NewArchitecture:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    created_by_user_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class Architecture:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    status: ArchitectureStatus
    current_revision: int
    created_by_user_id: uuid.UUID | None
    updated_by_user_id: uuid.UUID | None
    archived_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def is_archived(self) -> bool:
        return self.status is ArchitectureStatus.ARCHIVED

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def ensure_modifiable(self) -> None:
        """The write guard for the architecture's metadata, content and layout."""
        if self.is_deleted:
            raise ArchitectureNotFound
        if self.is_archived:
            raise ArchitectureArchived

    def with_metadata(self, *, name: str | None, description: str | None, by: uuid.UUID) -> Architecture:
        """Name and description only; never the content, the project or the lifecycle."""
        self.ensure_modifiable()
        return replace(
            self,
            name=normalize_architecture_name(name) if name is not None else self.name,
            description=normalize_architecture_description(description)
            if description is not None
            else self.description,
            updated_by_user_id=by,
        )

    def archive(self, at: datetime, *, by: uuid.UUID) -> Architecture:
        if self.is_deleted:
            raise ArchitectureNotFound
        if self.is_archived:
            return self  # idempotent: the original archive time is kept
        return replace(self, status=ArchitectureStatus.ARCHIVED, archived_at=at, updated_by_user_id=by)

    def restore(self, *, by: uuid.UUID) -> Architecture:
        if self.is_deleted:
            raise ArchitectureNotFound
        if not self.is_archived:
            return self  # idempotent
        return replace(self, status=ArchitectureStatus.ACTIVE, archived_at=None, updated_by_user_id=by)

    def delete(self, at: datetime, *, by: uuid.UUID) -> Architecture:
        if self.is_deleted:
            raise ArchitectureNotFound
        if not self.is_archived:
            raise ArchitectureNotArchived
        return replace(self, deleted_at=at, updated_by_user_id=by)


@dataclass(frozen=True, slots=True)
class ArchitectureQuery:
    """Listing within a project: newest first, keyset-paginated, optionally filtered."""

    status: ArchitectureStatus | None = None
    search: str | None = None  # case-insensitive substring of the name
    after: tuple[datetime, uuid.UUID] | None = None  # the last (created_at, id) of the previous page
    limit: int = 50


@dataclass(frozen=True, slots=True)
class RevisionSummary:
    """A revision without its content, for history listings."""

    number: int
    parent_number: int | None
    restored_from: int | None
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
