import uuid
from datetime import UTC, datetime

import pytest

from core.domain.pagination import InvalidCursor, encode_cursor
from core.domain.requirements.queries import RequirementCursor, RequirementQuery


def test_cursor_round_trip() -> None:
    cursor = RequirementCursor(created_at=datetime(2026, 9, 25, 12, tzinfo=UTC), id=uuid.uuid7())
    assert RequirementCursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize(
    "raw",
    [
        "not-base64!",
        encode_cursor(["name", "2026-09-25T12:00:00+00:00", str(uuid.uuid7())]),  # a project cursor
        encode_cursor(["created_at", "2026-09-25T12:00:00", str(uuid.uuid7())]),  # naive
        encode_cursor(["created_at", "yesterday", str(uuid.uuid7())]),
        encode_cursor(["created_at", "2026-09-25T12:00:00+00:00", "REQ-1"]),
    ],
)
def test_foreign_or_malformed_cursors_are_refused(raw: str) -> None:
    with pytest.raises(InvalidCursor):
        RequirementCursor.decode(raw)


def test_search_is_bounded() -> None:
    with pytest.raises(ValueError, match="search"):
        RequirementQuery(search="x" * 101)
