"""Deterministic normalization of requirement input, and the canonical form engines consume.

Input normalization rewrites convenient spellings into the strict stored shape before it is
parsed (``value_objects.parse_structured_data`` stays strict). Nothing is guessed: a spelling
is either in the tables below or refused.

    quantity   "2k requests/sec" -> value "2000", unit "requests/second"
               "300 ms"          -> value "300",  unit "ms"
               "99.9%"           -> value "99.9", unit "%"      (canonical: ratio 0.999)
               "10M users"       -> value "10000000", unit "users"
    value      "2k" -> "2000", "1.5M" -> "1500000", "2bn" -> "2000000000", "2,000" -> "2000"
    unit       "rps", "req/s", "requests per second" -> "requests/second"; "seconds" -> "s";
               "percent" -> "%"; "usd/month" -> "USD/month"
    percentile "p95" -> "95"
    metric     "rps" -> "requests_per_second", "dau" -> "daily_active_users"

Refused as ambiguous: a lower-case "m" (milli, million or minutes?), "b"/"B" as a magnitude
(billion or bytes?), lower-case data sizes like "gb" (bits or bytes?), "$" (which dollar?),
and decimal commas ("2,5"). Magnitude suffixes must touch the number ("2k", not "2 k").

The canonical form converts every quantity to its dimension's canonical unit (requests/second,
users, ms, ratio, B; money keeps its currency). Comparisons use exact fractions; displayed
canonical values are exact decimals, or rounded to 9 places (half-even) when the conversion does
not terminate (e.g. 1000 requests/minute = 16.666666667 requests/second).
"""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Any

from .value_objects import (
    CANONICAL_UNITS,
    MAX_DECIMAL_PLACES,
    Operator,
    QuantityConstraint,
    RangeConstraint,
    SetConstraint,
    StructuredConstraint,
    Unit,
    decimal_places,
    decimal_to_str,
    invalid,
)

_MAGNITUDES = {"k": 10**3, "K": 10**3, "M": 10**6, "bn": 10**9}
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_VALUE = re.compile(rf"^\s*(?P<number>{_NUMBER})(?P<suffix>k|K|M|bn)?\s*$")
_QUANTITY = re.compile(rf"^\s*(?P<number>{_NUMBER})(?:(?P<suffix>k|K|M|bn)(?=\s|$))?\s*(?P<unit>.*?)\s*$")
_PERCENTILE = re.compile(r"^\s*[pP](?P<number>\d+(?:\.\d+)?)\s*$")
_MONEY = re.compile(r"^(?P<currency>[A-Za-z]{3})\s*/\s*(?:month|mo)$", re.IGNORECASE)

# Exact spellings (case-sensitive): symbols whose case carries meaning.
_EXACT_UNITS = {
    "requests/second": "requests/second",
    "requests/minute": "requests/minute",
    "requests/hour": "requests/hour",
    "requests/day": "requests/day",
    "RPS": "requests/second",
    "ms": "ms",
    "s": "s",
    "min": "min",
    "h": "h",
    "d": "d",
    "%": "%",
    "ratio": "ratio",
    "B": "B",
    "KB": "KB",
    "MB": "MB",
    "GB": "GB",
    "TB": "TB",
    "PB": "PB",
    "users": "users",
}
# Words, matched case-insensitively with whitespace collapsed.
_WORD_UNITS = {
    **{
        alias: "requests/second"
        for alias in (
            "rps",
            "req/s",
            "req/sec",
            "requests/sec",
            "request/second",
            "requests per second",
            "requests/s",
        )
    },
    **{alias: "requests/minute" for alias in ("rpm", "req/min", "requests/min", "requests per minute")},
    **{alias: "requests/hour" for alias in ("req/h", "req/hour", "requests/h", "requests per hour")},
    **{alias: "requests/day" for alias in ("req/day", "requests/d", "requests per day")},
    **{alias: "users" for alias in ("user", "users")},
    **{alias: "ms" for alias in ("msec", "msecs", "millisecond", "milliseconds")},
    **{alias: "s" for alias in ("sec", "secs", "second", "seconds")},
    **{alias: "min" for alias in ("mins", "minute", "minutes")},
    **{alias: "h" for alias in ("hr", "hrs", "hour", "hours")},
    **{alias: "d" for alias in ("day", "days")},
    **{alias: "%" for alias in ("percent", "pct")},
    **{alias: "B" for alias in ("byte", "bytes")},
    **{
        alias: "orders/second"
        for alias in ("orders/s", "orders/sec", "order/second", "orders per second", "orders/second")
    },
    **{alias: "orders/minute" for alias in ("orders/min", "orders per minute", "orders/minute")},
    **{alias: "orders/hour" for alias in ("orders/h", "orders per hour", "orders/hour")},
    **{alias: "orders/day" for alias in ("orders/d", "orders per day", "orders/day")},
    # The spec's canonical unit names, accepted as spellings of the stored symbols.
    "requests_per_second": "requests/second",
    "orders_per_second": "orders/second",
    "percentage": "%",
}
_AMBIGUOUS_UNITS = {"m", "b", "kb", "mb", "gb", "tb", "pb", "$", "$/month"}
_METRIC_ALIASES = {
    "rps": "requests_per_second",
    "dau": "daily_active_users",
    "mau": "monthly_active_users",
    "ccu": "concurrent_users",
    "ops": "orders_per_second",
}
_OPERATOR_ALIASES = {
    "=": "==",
    "eq": "==",
    "gte": ">=",
    "gt": ">",
    "lte": "<=",
    "lt": "<",
    "range": "between",
}


def normalize_unit(raw: str) -> str:
    """The explicit unit symbol for a spelling, or InvalidRequirement."""
    text = " ".join(raw.split())
    if text in _EXACT_UNITS:
        return _EXACT_UNITS[text]
    folded = text.lower()
    if folded in _WORD_UNITS:
        return _WORD_UNITS[folded]
    money = _MONEY.fullmatch(text)
    if money:
        return f"{money['currency'].upper()}/month"
    if folded in _AMBIGUOUS_UNITS:
        raise invalid("structured_data.unit", "ambiguous_unit")
    return text  # left for the strict parser to accept (e.g. "EUR/month") or refuse


def _number(number: str, suffix: str | None) -> str:
    value = Decimal(number.replace(",", "")) * _MAGNITUDES.get(suffix or "", 1)
    return decimal_to_str(value)


def normalize_value(raw: object) -> object:
    """Strings with thousands separators or a magnitude suffix become plain decimal strings;
    anything else is left for the strict parser."""
    if not isinstance(raw, str):
        return raw
    match = _VALUE.fullmatch(raw)
    if match is None:
        return raw
    return _number(match["number"], match["suffix"])


def parse_quantity(text: str) -> tuple[str, str]:
    """ "2k requests/sec" -> ("2000", "requests/second")."""
    match = _QUANTITY.fullmatch(text)
    if match is None:
        raise invalid("structured_data.quantity", "not_a_quantity")
    if not match["unit"]:
        raise invalid("structured_data.quantity", "unit_required")
    return _number(match["number"], match["suffix"]), normalize_unit(match["unit"])


def normalize_structured_data(raw: object) -> object:
    """Input form -> strict stored form (see the module docstring). Anything this does not
    recognize is passed through unchanged, for the strict parser to accept or refuse."""
    if not isinstance(raw, dict) or not raw:
        return raw
    data: dict[str, Any] = dict(raw)
    if "quantity" in data:
        if "value" in data or "unit" in data:
            raise invalid("structured_data.quantity", "conflicts_with_value_and_unit")
        quantity = data.pop("quantity")
        if not isinstance(quantity, str):
            raise invalid("structured_data.quantity", "not_a_quantity")
        data["value"], data["unit"] = parse_quantity(quantity)
    if isinstance(data.get("unit"), str):
        data["unit"] = normalize_unit(data["unit"])
    for key in ("value", "min", "max"):
        if key in data:
            data[key] = normalize_value(data[key])
    if isinstance(data.get("operator"), str):
        operator = " ".join(data["operator"].split()).lower()
        data["operator"] = _OPERATOR_ALIASES.get(operator, data["operator"])
    if isinstance(data.get("percentile"), str):
        percentile = _PERCENTILE.fullmatch(data["percentile"])
        if percentile:
            data["percentile"] = percentile["number"]
    if isinstance(data.get("metric"), str):
        metric = data["metric"].strip().lower()
        data["metric"] = _METRIC_ALIASES.get(metric, data["metric"])
    return data


# --- canonical form ------------------------------------------------------------------------------


def _display(exact: Fraction) -> Decimal:
    """Exact when the value terminates within 9 decimal places, else rounded half-even."""
    decimal = Decimal(exact.numerator) / Decimal(exact.denominator)
    if Fraction(decimal) != exact or decimal_places(decimal) > MAX_DECIMAL_PLACES:
        decimal = decimal.quantize(Decimal(1).scaleb(-MAX_DECIMAL_PLACES), rounding=ROUND_HALF_EVEN)
    return decimal.normalize() + 0


@dataclass(frozen=True, slots=True)
class Interval:
    """The values a constraint allows, exactly; ``None`` is unbounded on that side."""

    lower: Fraction | None
    lower_inclusive: bool
    upper: Fraction | None
    upper_inclusive: bool

    def intersects(self, other: Interval) -> bool:
        lower, lower_open = _tighter_lower(self, other)
        upper, upper_open = _tighter_upper(self, other)
        if lower is None or upper is None:
            return True
        return lower < upper or (lower == upper and not (lower_open or upper_open))

    def contains(self, other: Interval) -> bool:
        """Every value ``other`` allows, this allows too (``other`` is at least as strict)."""
        own_floor = (other.lower, not other.lower_inclusive) if other.lower is not None else (None, False)
        own_ceiling = (other.upper, not other.upper_inclusive) if other.upper is not None else (None, False)
        return _tighter_lower(self, other) == own_floor and _tighter_upper(self, other) == own_ceiling


def _tighter_lower(a: Interval, b: Interval) -> tuple[Fraction | None, bool]:
    """The higher of two floors, and whether it is open (excludes its value)."""
    candidates = [(i.lower, not i.lower_inclusive) for i in (a, b) if i.lower is not None]
    if not candidates:
        return None, False
    value = max(v for v, _ in candidates)
    return value, any(open_ for v, open_ in candidates if v == value)


def _tighter_upper(a: Interval, b: Interval) -> tuple[Fraction | None, bool]:
    candidates = [(i.upper, not i.upper_inclusive) for i in (a, b) if i.upper is not None]
    if not candidates:
        return None, False
    value = min(v for v, _ in candidates)
    return value, any(open_ for v, open_ in candidates if v == value)


@dataclass(frozen=True, slots=True)
class CanonicalQuantity:
    """A quantity or range constraint in its dimension's canonical unit."""

    metric: str
    operator: Operator
    exact: Fraction  # the value, or the range's minimum
    unit: str
    percentile: Decimal | None
    exact_max: Fraction | None = None  # the range's maximum

    @property
    def value(self) -> Decimal:
        return _display(self.exact)

    @property
    def is_exact(self) -> bool:
        return Fraction(self.value) == self.exact and (
            self.exact_max is None or Fraction(_display(self.exact_max)) == self.exact_max
        )

    @property
    def interval(self) -> Interval:
        match self.operator:
            case Operator.AT_LEAST | Operator.MORE_THAN:
                return Interval(self.exact, self.operator is Operator.AT_LEAST, None, False)
            case Operator.AT_MOST | Operator.LESS_THAN:
                return Interval(None, False, self.exact, self.operator is Operator.AT_MOST)
            case Operator.BETWEEN:
                return Interval(self.exact, True, self.exact_max, True)
            case _:  # EQUALS
                return Interval(self.exact, True, self.exact, True)

    def describe(self) -> str:
        """Human-readable bound, e.g. ">= 2000 requests/second" or "between 10 and 20 B"."""
        if self.exact_max is not None:
            maximum = decimal_to_str(_display(self.exact_max))
            return f"between {decimal_to_str(self.value)} and {maximum} {self.unit}"
        return f"{self.operator.value} {decimal_to_str(self.value)} {self.unit}"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"metric": self.metric, "operator": self.operator.value}
        if self.exact_max is not None:
            data |= {"min": decimal_to_str(self.value), "max": decimal_to_str(_display(self.exact_max))}
        else:
            data["value"] = decimal_to_str(self.value)
        data["unit"] = self.unit
        if self.percentile is not None:
            data["percentile"] = decimal_to_str(self.percentile)
        return data


def exact_canonical(unit: Unit, value: Decimal) -> Fraction:
    return Fraction(value) * unit.factor / unit.divisor


def canonical(constraint: QuantityConstraint | RangeConstraint) -> CanonicalQuantity:
    unit = CANONICAL_UNITS.get(constraint.unit.dimension, constraint.unit.symbol)
    if isinstance(constraint, RangeConstraint):
        return CanonicalQuantity(
            metric=constraint.metric,
            operator=Operator.BETWEEN,
            exact=exact_canonical(constraint.unit, constraint.minimum),
            unit=unit,
            percentile=constraint.percentile,
            exact_max=exact_canonical(constraint.unit, constraint.maximum),
        )
    return CanonicalQuantity(
        metric=constraint.metric,
        operator=constraint.operator,
        exact=exact_canonical(constraint.unit, constraint.value),
        unit=unit,
        percentile=constraint.percentile,
    )


def canonical_data(constraint: StructuredConstraint | None) -> dict[str, Any] | None:
    """The canonical form as JSON-ready data (sets are already canonical: sorted, lower-case)."""
    if constraint is None:
        return None
    if isinstance(constraint, SetConstraint):
        return constraint.to_dict()
    return canonical(constraint).to_dict()
