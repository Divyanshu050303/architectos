"""Checking a model's structured output against the schema it was asked to follow.

A provider's schema support is a convenience, not a guarantee: some constraints (lengths, counts,
ranges, patterns) are not enforced by every provider, and a provider can be replaced by one with no
schema support at all. So the caller sends ``relaxed(schema)`` — the shape only — and checks the
answer against the full ``schema`` with ``problems``, here, deterministically.

Only the keywords below are supported; a schema using any other is refused when it is checked
(``UnsupportedSchema``), so a constraint can never be silently ignored.
"""

import re
from collections.abc import Callable
from typing import Any

SUPPORTED = frozenset(
    {
        "type", "enum", "properties", "required", "additionalProperties", "items", "anyOf",
        "maxItems", "minItems", "maxLength", "minLength", "pattern", "minimum", "maximum", "description",
    }
)  # fmt: skip
CONSTRAINTS = frozenset({"maxItems", "minLength", "maxLength", "pattern", "minimum", "maximum"})
MAX_PROBLEMS = 20


class UnsupportedSchema(ValueError):
    pass


_TYPES: dict[str, Callable[[object], bool]] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _is_type(value: object, kind: str) -> bool:
    if kind not in _TYPES:
        raise UnsupportedSchema(f"type {kind}")
    return _TYPES[kind](value)


def _scalar(value: object, schema: dict[str, Any]) -> str | None:
    if isinstance(value, str):
        if len(value) > schema.get("maxLength", len(value)) or len(value) < schema.get("minLength", 0):
            return "has the wrong length"
        if "pattern" in schema and not re.search(schema["pattern"], value):
            return "is not in the expected format"
    elif isinstance(value, int | float) and not isinstance(value, bool):
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            return "is out of range"
    return None


def _array(value: list[Any], schema: dict[str, Any], path: str, found: list[tuple[str, str]]) -> None:
    limit = schema.get("maxItems", len(value))
    if len(value) > limit or len(value) < schema.get("minItems", 0):
        found.append((path, "has the wrong number of items"))
    for index, item in enumerate(value[:limit]):
        if "items" in schema:
            _check(item, schema["items"], f"{path}[{index}]", found)


def _object(value: dict[str, Any], schema: dict[str, Any], path: str, found: list[tuple[str, str]]) -> None:
    properties: dict[str, Any] = schema.get("properties", {})
    for name in schema.get("required", ()):
        if name not in value:
            found.append((f"{path}.{name}", "is required"))
    for name, item in value.items():
        if name in properties:
            _check(item, properties[name], f"{path}.{name}", found)
        elif schema.get("additionalProperties", True) is False:
            found.append((path, "has an unexpected field"))  # its name is model output: not echoed


def _check(value: object, schema: dict[str, Any], path: str, found: list[tuple[str, str]]) -> None:
    unsupported = set(schema) - SUPPORTED
    if unsupported:
        raise UnsupportedSchema(", ".join(sorted(unsupported)))
    if "anyOf" in schema:
        if not any(not problems(value, option) for option in schema["anyOf"]):
            found.append((path, "matches none of the allowed shapes"))
        return
    kinds = schema.get("type")
    allowed = [] if kinds is None else kinds if isinstance(kinds, list) else [kinds]
    if allowed and not any(_is_type(value, kind) for kind in allowed):
        found.append((path, f"must be {' or '.join(allowed)}"))
        return
    if "enum" in schema and value not in schema["enum"]:
        found.append((path, "is not an allowed value"))
    problem = _scalar(value, schema)
    if problem:
        found.append((path, problem))
    if isinstance(value, list):
        _array(value, schema, path, found)
    elif isinstance(value, dict):
        _object(value, schema, path, found)


def problems(value: object, schema: dict[str, Any]) -> list[tuple[str, str]]:
    """(path, problem) for each way ``value`` breaks ``schema``; empty when it conforms. Paths are
    built from the schema's own names and indexes only — never from the value's text."""
    found: list[tuple[str, str]] = []
    _check(value, schema, "$", found)
    return found[:MAX_PROBLEMS]


def relaxed(schema: Any) -> Any:
    """The schema without length, count, range or pattern constraints: the shape a provider enforces."""
    if isinstance(schema, list):
        return [relaxed(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    shape = {k: relaxed(v) for k, v in schema.items() if k not in CONSTRAINTS and k != "properties"}
    if "properties" in schema:  # property names are names, not keywords: none is dropped
        shape["properties"] = {name: relaxed(v) for name, v in schema["properties"].items()}
    return shape
