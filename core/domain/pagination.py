"""Keyset pagination shared by every list endpoint.

A cursor is opaque to clients: base64url of a JSON array of strings (the sort name, the last
row's sort value and its id). It carries the sort it was issued for, so a cursor cannot be
replayed against a different ordering. Pages are fetched with limit + 1 rows to know whether
another page exists, without a COUNT query.
"""

import base64
import binascii
import json
from dataclasses import dataclass

from core.domain.errors import DomainError

MAX_PAGE_SIZE = 100


class InvalidCursor(DomainError):
    code = "invalid_cursor"
    message = "The pagination cursor is invalid."


def encode_cursor(parts: list[str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(parts, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(value: str, *, length: int) -> list[str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        parts = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except ValueError, binascii.Error, UnicodeDecodeError:
        raise InvalidCursor from None
    if not isinstance(parts, list) or len(parts) != length or not all(isinstance(p, str) for p in parts):
        raise InvalidCursor
    return parts


def page_size(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_SIZE))


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None
