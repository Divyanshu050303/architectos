"""Reading a stored migration plan back: every part of a proposal and request is rebuilt from the
dictionary its own ``to_dict`` produced, field by field from the dataclass's type hints — enums,
ids, timestamps, tuples, nested parts — and validated again by its own constructor. Keys a
``to_dict`` adds for display (ids derived from content, summaries, the status) are ignored: they
are recomputed. A stored proposal whose fingerprint no longer matches its content is refused.
"""

import dataclasses
import types
import typing
import uuid
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any, cast

from .entities import MigrationRequest, ReviewEvent
from .errors import InvalidMigrationPlan
from .plans import MigrationProposal


def _value(hint: Any, raw: Any) -> Any:  # noqa: PLR0911 -- one return per kind of field
    if raw is None:
        return None
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in args if a is not type(None)]
        return _value(inner[0], raw) if len(inner) == 1 else raw
    if origin is tuple:
        return tuple(_value(args[0], item) for item in raw)
    if origin in (Mapping, dict) or hint in (Mapping, dict):
        return dict(raw)
    if isinstance(hint, type):
        if issubclass(hint, Enum):
            return hint(raw)
        if hint is uuid.UUID:
            return uuid.UUID(raw)
        if hint is datetime:
            return datetime.fromisoformat(raw)
        if dataclasses.is_dataclass(hint):
            return from_dict(hint, raw)
    return raw


def from_dict[T](cls: type[T], data: Mapping[str, Any]) -> T:
    """``cls`` rebuilt from its ``to_dict`` output (unknown keys ignored, missing ones defaulted)."""
    hints = typing.get_type_hints(cls)
    fields = {f.name for f in dataclasses.fields(cast(Any, cls)) if f.init}
    values = {name: _value(hints[name], data[name]) for name in fields if name in data}
    return cls(**values)


def proposal_from_dict(data: Mapping[str, Any], fingerprint: str) -> MigrationProposal:
    """A stored proposal, verified against the fingerprint stored with it."""
    proposal = from_dict(MigrationProposal, data)
    if proposal.fingerprint != fingerprint:
        raise InvalidMigrationPlan(details={"fields": ["fingerprint"]})
    return proposal


def request_from_dict(data: Mapping[str, Any]) -> MigrationRequest:
    return from_dict(MigrationRequest, data)


def reviews_from_list(data: list[Any]) -> tuple[ReviewEvent, ...]:
    return tuple(from_dict(ReviewEvent, r) for r in data)
