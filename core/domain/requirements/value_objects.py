"""Requirement values: text, numbers, units and the structured constraint. This module checks the
*shape* of a value; whether it makes sense for a requirement (metric allowed for the type, unit
matches the metric, value in range) is decided by the rules in ``requirements.py``.

Numbers are Decimal end to end, never float: 99.9 % must not become 0.9989999. A float from a
JSON parser is converted through its shortest repr, so 99.9 stays exactly "99.9". Stored numbers
are decimal strings (``"2000"``, ``"0.999"``) so JSON round-trips cannot lose precision.

Units are explicit and closed. Each belongs to a dimension with one canonical unit, and
conversion to it is exact multiplication/division:

    rate        requests/second (canonical), requests/minute, requests/hour, requests/day
    count       users
    duration    ms (canonical), s, min, h, d
    ratio       ratio (canonical, 0.999), % (99.9 % = 0.999)
    data size   B (canonical), KB, MB, GB, TB, PB  (decimal: 1 KB = 1000 B)
    money       <ISO 4217>/month, e.g. USD/month; currencies are never converted
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum, StrEnum, auto
from typing import Any

from core.domain.text import has_forbidden_characters

from .errors import InvalidRequirement

MAX_TITLE_LENGTH = 200
MAX_STATEMENT_LENGTH = 5000
MAX_CATEGORY_LENGTH = 64
MAX_CHANGE_REASON_LENGTH = 500
IDENTIFIER_FORMAT = re.compile(r"^[a-z][a-z0-9_]*$")  # categories and metrics
MAX_SET_VALUES = 50
SET_VALUE_FORMAT = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")  # e.g. cloud regions: eu-west-1
MAX_DECIMAL_PLACES = 9
MAX_MAGNITUDE = Decimal(10) ** 15
CONFIDENCE_PLACES = 3
_ALLOWED_TEXT_CONTROLS = frozenset({"\n", "\t"})
_MONEY_UNIT = re.compile(r"^[A-Z]{3}/month$")


def invalid(field: str, reason: str) -> InvalidRequirement:
    return InvalidRequirement(details={"field": field, "reason": reason})


# --- text ----------------------------------------------------------------------------------------


def normalize_title(raw: str) -> str:
    title = unicodedata.normalize("NFC", " ".join(raw.split()))
    if not title or len(title) > MAX_TITLE_LENGTH:
        raise invalid("title", "length")
    if has_forbidden_characters(title):
        raise invalid("title", "control_characters")
    return title


def _normalize_block(raw: str, field: str, max_length: int) -> str:
    text = unicodedata.normalize("NFC", raw.replace("\r\n", "\n").strip())
    if not text or len(text) > max_length:
        raise invalid(field, "length")
    if has_forbidden_characters(text, _ALLOWED_TEXT_CONTROLS):
        raise invalid(field, "control_characters")
    return text


def normalize_statement(raw: str) -> str:
    return _normalize_block(raw, "statement", MAX_STATEMENT_LENGTH)


def normalize_change_reason(raw: str | None) -> str | None:
    """Blank means "no reason given"."""
    if raw is None or not raw.strip():
        return None
    return _normalize_block(raw, "change_reason", MAX_CHANGE_REASON_LENGTH)


def normalize_identifier(raw: object, field: str) -> str:
    """Categories and metric names: lower_snake_case, trimmed and lower-cased, never rewritten
    beyond that ("P95 latency" is refused, not turned into "p95_latency")."""
    if not isinstance(raw, str):
        raise invalid(field, "not_a_string")
    value = raw.strip().lower()
    if not value or len(value) > MAX_CATEGORY_LENGTH or not IDENTIFIER_FORMAT.fullmatch(value):
        raise invalid(field, "format")
    return value


# --- numbers -------------------------------------------------------------------------------------


def _decimal_places(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def parse_decimal(raw: object, field: str) -> Decimal:
    """An exact, finite number with at most 9 decimal places and magnitude below 10^15."""
    if isinstance(raw, bool):
        raise invalid(field, "not_a_number")
    try:
        match raw:
            case Decimal():
                value = raw
            case int():
                value = Decimal(raw)
            case float():
                value = Decimal(repr(raw))
            case str():
                value = Decimal(raw.strip())
            case _:
                raise invalid(field, "not_a_number")
    except InvalidOperation:
        raise invalid(field, "not_a_number") from None
    if not value.is_finite():
        raise invalid(field, "not_a_number")
    if abs(value) >= MAX_MAGNITUDE:
        raise invalid(field, "out_of_range")
    if _decimal_places(value) > MAX_DECIMAL_PLACES:
        raise invalid(field, "too_precise")
    return value.normalize() + 0  # canonical form; "+ 0" turns -0 into 0


def decimal_to_str(value: Decimal) -> str:
    """Plain notation, no exponent, no trailing zeros: Decimal("2E+3") -> "2000"."""
    return format(value.normalize(), "f")


def parse_confidence(raw: object) -> Decimal:
    """Confidence in the *interpretation* of a requirement (by an extractor), in [0, 1] with at most
    three decimals. It is not the probability that the requirement is true."""
    value = parse_decimal(raw, "confidence")
    if not Decimal(0) <= value <= Decimal(1):
        raise invalid("confidence", "out_of_range")
    if _decimal_places(value) > CONFIDENCE_PLACES:
        raise invalid("confidence", "too_precise")
    return value


# --- units ---------------------------------------------------------------------------------------


class Dimension(StrEnum):
    RATE = "rate"
    COUNT = "count"
    DURATION = "duration"
    RATIO = "ratio"
    DATA_SIZE = "data_size"
    MONEY_PER_MONTH = "money_per_month"


@dataclass(frozen=True, slots=True)
class Unit:
    symbol: str
    dimension: Dimension
    # canonical value = value * factor / divisor (both exact integers)
    factor: int = 1
    divisor: int = 1

    def to_canonical(self, value: Decimal) -> Decimal:
        return value * self.factor / self.divisor

    @property
    def canonical_symbol(self) -> str:
        return CANONICAL_UNITS.get(self.dimension, self.symbol)


_UNITS = [
    Unit("requests/second", Dimension.RATE),
    Unit("requests/minute", Dimension.RATE, divisor=60),
    Unit("requests/hour", Dimension.RATE, divisor=3_600),
    Unit("requests/day", Dimension.RATE, divisor=86_400),
    Unit("users", Dimension.COUNT),
    Unit("ms", Dimension.DURATION),
    Unit("s", Dimension.DURATION, factor=1_000),
    Unit("min", Dimension.DURATION, factor=60_000),
    Unit("h", Dimension.DURATION, factor=3_600_000),
    Unit("d", Dimension.DURATION, factor=86_400_000),
    Unit("ratio", Dimension.RATIO),
    Unit("%", Dimension.RATIO, divisor=100),
    Unit("B", Dimension.DATA_SIZE),
    Unit("KB", Dimension.DATA_SIZE, factor=10**3),
    Unit("MB", Dimension.DATA_SIZE, factor=10**6),
    Unit("GB", Dimension.DATA_SIZE, factor=10**9),
    Unit("TB", Dimension.DATA_SIZE, factor=10**12),
    Unit("PB", Dimension.DATA_SIZE, factor=10**15),
]
UNITS = {unit.symbol: unit for unit in _UNITS}
CANONICAL_UNITS = {
    Dimension.RATE: "requests/second",
    Dimension.COUNT: "users",
    Dimension.DURATION: "ms",
    Dimension.RATIO: "ratio",
    Dimension.DATA_SIZE: "B",
}


def unit_for(symbol: object) -> Unit:
    """Unit symbols are case-sensitive (MB is not mb) and never guessed."""
    if not isinstance(symbol, str):
        raise invalid("structured_data.unit", "not_a_string")
    symbol = symbol.strip()
    if symbol in UNITS:
        return UNITS[symbol]
    if _MONEY_UNIT.fullmatch(symbol):
        return Unit(symbol, Dimension.MONEY_PER_MONTH)
    raise invalid("structured_data.unit", "unknown_unit")


# --- structured constraint -----------------------------------------------------------------------


class Operator(StrEnum):
    AT_LEAST = ">="
    MORE_THAN = ">"
    AT_MOST = "<="
    LESS_THAN = "<"
    ONE_OF = "in"  # set constraints only

    @property
    def is_lower_bound(self) -> bool:
        return self in {Operator.AT_LEAST, Operator.MORE_THAN}

    @property
    def is_upper_bound(self) -> bool:
        return self in {Operator.AT_MOST, Operator.LESS_THAN}


@dataclass(frozen=True, slots=True)
class QuantityConstraint:
    """A measurable bound, e.g. requests_per_second >= 2000 requests/second, or p95 latency <= 300 ms."""

    metric: str
    operator: Operator
    value: Decimal
    unit: Unit
    percentile: Decimal | None = None

    @property
    def canonical_value(self) -> Decimal:
        return self.unit.to_canonical(self.value)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "metric": self.metric,
            "operator": self.operator.value,
            "value": decimal_to_str(self.value),
            "unit": self.unit.symbol,
        }
        if self.percentile is not None:
            data["percentile"] = decimal_to_str(self.percentile)
        return data


@dataclass(frozen=True, slots=True)
class SetConstraint:
    """A membership constraint, e.g. regions in {eu-central-1, eu-west-1}. Values are kept sorted,
    so equal sets have one representation."""

    metric: str
    values: tuple[str, ...]

    @property
    def operator(self) -> Operator:
        return Operator.ONE_OF

    def to_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "operator": Operator.ONE_OF.value, "values": list(self.values)}


type StructuredConstraint = QuantityConstraint | SetConstraint

_QUANTITY_KEYS = {"metric", "operator", "value", "unit", "percentile"}
_SET_KEYS = {"metric", "operator", "values"}


def _reject_unknown_keys(raw: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise invalid(f"structured_data.{unknown[0]}", "unknown_field")


def _require(raw: dict[str, Any], key: str) -> object:
    if raw.get(key) is None:
        raise invalid(f"structured_data.{key}", "required")
    return raw[key]


def parse_structured_data(raw: object) -> StructuredConstraint | None:
    """``{}`` means "no structured constraint". Anything else must be exactly one well-formed
    constraint; unknown keys are refused, so structured data never becomes an untyped bag."""
    if not isinstance(raw, dict):
        raise invalid("structured_data", "not_an_object")
    if not raw:
        return None
    if not all(isinstance(key, str) for key in raw):
        raise invalid("structured_data", "not_an_object")
    metric = normalize_identifier(_require(raw, "metric"), "structured_data.metric")
    raw_operator = _require(raw, "operator")
    if not isinstance(raw_operator, str) or raw_operator not in {o.value for o in Operator}:
        raise invalid("structured_data.operator", "unknown_operator")
    operator = Operator(raw_operator)

    if operator is Operator.ONE_OF:
        _reject_unknown_keys(raw, _SET_KEYS)
        return SetConstraint(metric=metric, values=_parse_set_values(_require(raw, "values")))

    _reject_unknown_keys(raw, _QUANTITY_KEYS)
    percentile = raw.get("percentile")
    parsed_percentile = None
    if percentile is not None:
        parsed_percentile = parse_decimal(percentile, "structured_data.percentile")
        if not Decimal(0) < parsed_percentile <= Decimal(100):
            raise invalid("structured_data.percentile", "out_of_range")
    return QuantityConstraint(
        metric=metric,
        operator=operator,
        value=parse_decimal(_require(raw, "value"), "structured_data.value"),
        unit=unit_for(_require(raw, "unit")),
        percentile=parsed_percentile,
    )


def _parse_set_values(raw: object) -> tuple[str, ...]:
    field = "structured_data.values"
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SET_VALUES:
        raise invalid(field, "length")
    values: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not SET_VALUE_FORMAT.fullmatch(item.strip().lower()):
            raise invalid(field, "format")
        value = item.strip().lower()
        if value in values:
            raise invalid(field, "duplicate")
        values.add(value)
    return tuple(sorted(values))


class Keep(Enum):
    """Marks a field an update leaves unchanged (distinct from ``{}``, which clears structured data)."""

    KEEP = auto()


KEEP = Keep.KEEP
