"""Requirement listing: filters and the keyset cursor (newest first, on (created_at, id))."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from core.domain import pagination
from core.domain.pagination import InvalidCursor

from .enums import RequirementPriority, RequirementStatus, RequirementType

MAX_SEARCH_LENGTH = 100
_KIND = "created_at"


@dataclass(frozen=True, slots=True)
class RequirementCursor:
    created_at: datetime
    id: uuid.UUID

    def encode(self) -> str:
        return pagination.encode_cursor([_KIND, self.created_at.isoformat(), str(self.id)])

    @classmethod
    def decode(cls, raw: str) -> RequirementCursor:
        kind, created_at, requirement_id = pagination.decode_cursor(raw, length=3)
        try:
            if kind != _KIND:
                raise ValueError("cursor was issued for another list")
            at = datetime.fromisoformat(created_at)
            if at.tzinfo is None:
                raise ValueError("naive timestamp")
            return cls(created_at=at, id=uuid.UUID(requirement_id))
        except ValueError:
            raise InvalidCursor from None


@dataclass(frozen=True, slots=True)
class RequirementQuery:
    """Filters combine with AND; ``search`` matches title or statement, case-insensitively."""

    type: RequirementType | None = None
    category: str | None = None
    status: RequirementStatus | None = None
    priority: RequirementPriority | None = None
    search: str | None = None
    after: RequirementCursor | None = None
    limit: int = 50

    def __post_init__(self) -> None:
        if self.search is not None and len(self.search) > MAX_SEARCH_LENGTH:
            raise ValueError("search too long")
