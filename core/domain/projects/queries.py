"""Project listing: filters, the whitelisted sort orders and their keyset cursors."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from core.domain import pagination
from core.domain.pagination import InvalidCursor

from .enums import ProjectStatus

MAX_SEARCH_LENGTH = 100


class ProjectSort(StrEnum):
    """The only orderings a client can request. Newest first unless sorting by name."""

    CREATED_AT = "created_at"  # newest first
    UPDATED_AT = "updated_at"  # most recently changed first
    NAME = "name"  # alphabetical, case-insensitive


@dataclass(frozen=True, slots=True)
class ProjectCursor:
    sort: ProjectSort
    # created_at/updated_at as ISO 8601, or the lower-cased name
    value: str
    id: uuid.UUID

    def encode(self) -> str:
        return pagination.encode_cursor([self.sort.value, self.value, str(self.id)])

    @classmethod
    def decode(cls, raw: str, *, sort: ProjectSort) -> ProjectCursor:
        kind, value, project_id = pagination.decode_cursor(raw, length=3)
        try:
            if kind != sort.value:
                raise ValueError("cursor was issued for another sort order")
            if sort is not ProjectSort.NAME and datetime.fromisoformat(value).tzinfo is None:
                raise ValueError("naive timestamp")
            return cls(sort=sort, value=value, id=uuid.UUID(project_id))
        except ValueError:
            raise InvalidCursor from None


@dataclass(frozen=True, slots=True)
class ProjectQuery:
    status: ProjectStatus | None = None
    search: str | None = None
    sort: ProjectSort = ProjectSort.CREATED_AT
    after: ProjectCursor | None = None
    limit: int = 50

    def __post_init__(self) -> None:
        if self.search is not None and len(self.search) > MAX_SEARCH_LENGTH:
            raise ValueError("search too long")
