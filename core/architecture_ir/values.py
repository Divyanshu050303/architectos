"""Value rules shared by the IR types: identifiers, text, exact numbers, metadata and preserved
JSON. The IR depends only on the standard library and ``core.domain.text``, so any engine can use
it without depending on another domain.

Each check returns violations instead of raising, so a caller can report every problem at once.
"""

import re
import unicodedata
from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from core.domain.text import has_forbidden_characters

from .errors import Violation

# Stable identifiers of nodes, connections and assumptions. Case-sensitive, never derived from
# display names; wide enough for imported ids ("aws_db_instance.main", "default/deployment/api").
ELEMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
METADATA_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 2000
MAX_METADATA_ENTRIES = 32
MAX_METADATA_VALUE_LENGTH = 500
MAX_DECIMAL_PLACES = 9
MAX_MAGNITUDE = Decimal(10) ** 15
_BLOCK_CONTROLS = frozenset({"\n", "\t"})

type Json = bool | int | str | tuple["Json", ...] | Mapping[str, "Json"] | None


def id_problems(value: object, field: str) -> list[Violation]:
    if isinstance(value, str) and ELEMENT_ID.fullmatch(value):
        return []
    return [
        Violation(
            "invalid_id",
            f"{field} must be 1-128 letters, digits or . _ : / - and start with a letter or digit.",
            field,
        )
    ]


# --- text ------------------------------------------------------------------------------------------


def clean_line(value: str) -> str:
    """One line of text: NFC, whitespace collapsed, trimmed."""
    return unicodedata.normalize("NFC", " ".join(value.split()))


def clean_block(value: str) -> str:
    """Multi-line text: NFC, CRLF → LF, trimmed."""
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").strip())


def text_problems(
    value: object, field: str, max_length: int, *, required: bool, block: bool = False
) -> list[Violation]:
    if value is None:
        return [Violation("required", f"{field} is required.", field)] if required else []
    if not isinstance(value, str):
        return [Violation("not_a_string", f"{field} must be text.", field)]
    if not value and required:
        return [Violation("required", f"{field} is required.", field)]
    if len(value) > max_length:
        return [Violation("too_long", f"{field} must be at most {max_length} characters.", field)]
    if has_forbidden_characters(value, _BLOCK_CONTROLS if block else frozenset()):
        return [Violation("control_characters", f"{field} contains control characters.", field)]
    return []


# --- numbers ---------------------------------------------------------------------------------------


def decimal_places(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def canonical_decimal(value: Decimal) -> Decimal:
    return value.normalize() + 0  # "+ 0" turns -0 into 0


def decimal_to_str(value: Decimal) -> str:
    """Plain notation, no exponent, no trailing zeros: Decimal("2E+3") -> "2000"."""
    return format(value.normalize(), "f")


def number_problems(value: object, field: str) -> list[Violation]:
    """An exact number: int or finite Decimal, at most 9 decimal places, magnitude below 10^15.
    Never bool, never float (99.9 must not become 99.8999…)."""
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return [Violation("not_a_number", f"{field} must be an exact number.", field)]
    return _exact_number_problems(value, field)


def _exact_number_problems(value: int | Decimal, field: str) -> list[Violation]:
    if isinstance(value, Decimal) and not value.is_finite():
        return [Violation("not_a_number", f"{field} must be a finite number.", field)]
    if abs(Decimal(value)) >= MAX_MAGNITUDE:
        return [Violation("out_of_range", f"{field} is too large.", field)]
    if isinstance(value, Decimal) and decimal_places(value) > MAX_DECIMAL_PLACES:
        return [
            Violation("too_precise", f"{field} has more than {MAX_DECIMAL_PLACES} decimal places.", field)
        ]
    return []


# --- metadata --------------------------------------------------------------------------------------


def frozen(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    """A read-only copy, keys sorted (so equal mappings serialize equally)."""
    return MappingProxyType(dict(sorted(mapping.items())))


def metadata_problems(metadata: object, field: str = "metadata") -> list[Violation]:
    """Free-form text labels (``{"team": "payments"}``): lower-case keys, short text values."""
    if not isinstance(metadata, Mapping):
        return [Violation("not_an_object", f"{field} must be an object of text values.", field)]
    problems: list[Violation] = []
    if len(metadata) > MAX_METADATA_ENTRIES:
        problems.append(
            Violation("too_many", f"{field} has more than {MAX_METADATA_ENTRIES} entries.", field)
        )
    for key, value in metadata.items():
        path = f"{field}.{key}"
        if not isinstance(key, str) or not METADATA_KEY.fullmatch(key):
            problems.append(
                Violation("invalid_key", f"{field} keys must be lower-case identifiers (e.g. team).", field)
            )
            continue
        problems += text_problems(value, path, MAX_METADATA_VALUE_LENGTH, required=True)
    return problems


# --- preserved JSON --------------------------------------------------------------------------------

MAX_JSON_DEPTH = 5
MAX_JSON_STRING = 1000
MAX_JSON_ITEMS = 100
MAX_JSON_INTEGER = 10**18


def freeze_json(value: Any) -> Json:
    """A deeply immutable copy of a JSON value (lists become tuples, objects read-only mappings)."""
    if isinstance(value, list | tuple):
        return tuple(freeze_json(v) for v in value)
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze_json(v) for k, v in sorted(value.items())})
    return value  # type: ignore[no-any-return]


def json_problems(value: object, field: str, depth: int = 0) -> list[Violation]:
    """Values kept as they were found (e.g. configuration nobody here understands): JSON scalars,
    arrays and objects, bounded in depth and size. Numbers must be integers: a fractional value is
    kept as text, so it can never lose precision."""
    if depth > MAX_JSON_DEPTH:
        return [Violation("too_deep", f"{field} is nested more than {MAX_JSON_DEPTH} levels.", field)]
    match value:
        case None | bool():
            problems: list[Violation] = []
        case int():
            problems = (
                []
                if abs(value) < MAX_JSON_INTEGER
                else [Violation("out_of_range", f"{field} is too large.", field)]
            )
        case str():
            problems = text_problems(value, field, MAX_JSON_STRING, required=False, block=True)
        case list() | tuple() | Mapping():
            problems = _container_problems(value, field, depth)
        case _:
            problems = [
                Violation(
                    "unsupported_value",
                    f"{field} must be text, an integer, true/false, null, a list or an object.",
                    field,
                )
            ]
    return problems


def _container_problems(
    value: list[Any] | tuple[Any, ...] | Mapping[Any, Any], field: str, depth: int
) -> list[Violation]:
    if len(value) > MAX_JSON_ITEMS:
        return [Violation("too_many", f"{field} has more than {MAX_JSON_ITEMS} items.", field)]
    if not isinstance(value, Mapping):
        return [p for i, item in enumerate(value) for p in json_problems(item, f"{field}[{i}]", depth + 1)]
    problems: list[Violation] = []
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            problems.append(Violation("invalid_key", f"{field} has an invalid key.", field))
            continue
        problems += json_problems(item, f"{field}.{key}", depth + 1)
    return problems
