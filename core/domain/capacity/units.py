"""Capacity quantities: exact numbers with explicit units.

Every quantity has a unit, and every unit belongs to one dimension; quantities of different
dimensions are never compared or combined. Values are exact decimals (never floats): a conversion
multiplies by an exact integer factor and divides by an exact integer divisor, so converting back
and forth loses nothing that the 9-decimal-place limit keeps. Presentation rounding is the reader's
business; results keep full precision.

Canonical units (what calculations use): requests/second, operations/second, events/second,
bytes/second, bytes, milliseconds, connections, users, cores, ratio. The rate, size and duration
units follow the requirements' unit table (``core/domain/requirements/value_objects.py``: the same
symbols and factors), extended with data rates, operations, events, connections and CPU.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Self

from core.domain.requirements.value_objects import (
    MAX_DECIMAL_PLACES,
    MAX_MAGNITUDE,
    decimal_places,
    decimal_to_str,
)

from .errors import InvalidQuantity


class Dimension(StrEnum):
    REQUEST_RATE = "request_rate"
    OPERATION_RATE = "operation_rate"  # database and cache operations
    EVENT_RATE = "event_rate"  # messages and events
    DATA_RATE = "data_rate"
    DATA_SIZE = "data_size"
    DURATION = "duration"
    CONNECTIONS = "connections"
    USERS = "users"
    CPU = "cpu"
    RATIO = "ratio"


@dataclass(frozen=True, slots=True)
class Unit:
    symbol: str
    dimension: Dimension
    factor: int = 1  # canonical = value * factor / divisor
    divisor: int = 1


_UNITS = [
    Unit("requests/second", Dimension.REQUEST_RATE),
    Unit("requests/minute", Dimension.REQUEST_RATE, divisor=60),
    Unit("requests/hour", Dimension.REQUEST_RATE, divisor=3_600),
    Unit("requests/day", Dimension.REQUEST_RATE, divisor=86_400),
    Unit("operations/second", Dimension.OPERATION_RATE),
    Unit("operations/minute", Dimension.OPERATION_RATE, divisor=60),
    Unit("events/second", Dimension.EVENT_RATE),
    Unit("events/minute", Dimension.EVENT_RATE, divisor=60),
    Unit("events/hour", Dimension.EVENT_RATE, divisor=3_600),
    Unit("B/s", Dimension.DATA_RATE),
    Unit("KB/s", Dimension.DATA_RATE, factor=10**3),
    Unit("MB/s", Dimension.DATA_RATE, factor=10**6),
    Unit("GB/s", Dimension.DATA_RATE, factor=10**9),
    Unit("B", Dimension.DATA_SIZE),
    Unit("KB", Dimension.DATA_SIZE, factor=10**3),
    Unit("MB", Dimension.DATA_SIZE, factor=10**6),
    Unit("GB", Dimension.DATA_SIZE, factor=10**9),
    Unit("TB", Dimension.DATA_SIZE, factor=10**12),
    Unit("ms", Dimension.DURATION),
    Unit("s", Dimension.DURATION, factor=1_000),
    Unit("min", Dimension.DURATION, factor=60_000),
    Unit("h", Dimension.DURATION, factor=3_600_000),
    Unit("d", Dimension.DURATION, factor=86_400_000),
    Unit("connections", Dimension.CONNECTIONS),
    Unit("users", Dimension.USERS),
    Unit("cores", Dimension.CPU),
    Unit("millicores", Dimension.CPU, divisor=1_000),
    Unit("ratio", Dimension.RATIO),
    Unit("%", Dimension.RATIO, divisor=100),
]
UNITS: dict[str, Unit] = {u.symbol: u for u in _UNITS}
CANONICAL: dict[Dimension, str] = {
    Dimension.REQUEST_RATE: "requests/second",
    Dimension.OPERATION_RATE: "operations/second",
    Dimension.EVENT_RATE: "events/second",
    Dimension.DATA_RATE: "B/s",
    Dimension.DATA_SIZE: "B",
    Dimension.DURATION: "ms",
    Dimension.CONNECTIONS: "connections",
    Dimension.USERS: "users",
    Dimension.CPU: "cores",
    Dimension.RATIO: "ratio",
}
PRECISION = Decimal(1).scaleb(-MAX_DECIMAL_PLACES)  # results keep 9 decimal places
RATES = frozenset({Dimension.REQUEST_RATE, Dimension.OPERATION_RATE, Dimension.EVENT_RATE})


def unit_for(symbol: object, field: str = "unit") -> Unit:
    """Symbols are case-sensitive (MB is not mb) and never guessed."""
    if not isinstance(symbol, str) or symbol.strip() not in UNITS:
        raise InvalidQuantity(details={"field": field, "reason": "unknown_unit"})
    return UNITS[symbol.strip()]


def exact(raw: object, field: str) -> Decimal:
    """An exact, finite, non-negative number: at most 9 decimal places, below 10^15. Floats are
    read through their shortest representation (0.1 is 0.1, not 0.1000000000000000055…)."""
    if isinstance(raw, bool):
        raise InvalidQuantity(details={"field": field, "reason": "not_a_number"})
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
                raise InvalidQuantity(details={"field": field, "reason": "not_a_number"})
    except InvalidOperation:
        raise InvalidQuantity(details={"field": field, "reason": "not_a_number"}) from None
    if not value.is_finite():
        raise InvalidQuantity(details={"field": field, "reason": "not_a_number"})
    if value < 0:
        raise InvalidQuantity(details={"field": field, "reason": "negative"})
    if value >= MAX_MAGNITUDE:
        raise InvalidQuantity(details={"field": field, "reason": "out_of_range"})
    if decimal_places(value) > MAX_DECIMAL_PLACES:
        raise InvalidQuantity(details={"field": field, "reason": "too_precise"})
    return value.normalize() + 0  # canonical form; "+ 0" turns -0 into 0


@dataclass(frozen=True, slots=True)
class Quantity:
    """A non-negative exact amount in a unit. Equal amounts in different units are different
    quantities but have equal ``canonical`` values."""

    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", exact(self.value, "value"))
        unit_for(self.unit)

    @classmethod
    def of(cls, value: object, unit: str, field: str = "value") -> Self:
        return cls(exact(value, field), unit_for(unit, f"{field}.unit").symbol)

    @classmethod
    def rounded(cls, value: Decimal, unit: str) -> Self:
        """A calculated amount, rounded half-even to the 9 decimal places a quantity keeps (the
        rounding is deterministic: Decimal arithmetic in the default context)."""
        return cls(value.quantize(PRECISION).normalize() + 0, unit_for(unit).symbol)

    @property
    def dimension(self) -> Dimension:
        return UNITS[self.unit].dimension

    @property
    def canonical(self) -> Decimal:
        """The value in the dimension's canonical unit, exact."""
        unit = UNITS[self.unit]
        return self.value * unit.factor / unit.divisor

    def to(self, symbol: str) -> Quantity:
        """The same amount in another unit of the same dimension (exact while it fits 9 decimal
        places; beyond that, rounded half-even at the 9th, and reported by ``exact``)."""
        target = unit_for(symbol)
        if target.dimension is not self.dimension:
            raise InvalidQuantity(details={"field": "unit", "reason": "incompatible_dimension"})
        return Quantity.rounded(self.canonical * target.divisor / target.factor, target.symbol)

    def canonical_quantity(self) -> Quantity:
        return self.to(CANONICAL[self.dimension])

    def require(self, *dimensions: Dimension, field: str = "value") -> Self:
        if self.dimension not in dimensions:
            raise InvalidQuantity(details={"field": field, "reason": "wrong_dimension"})
        return self

    def to_dict(self) -> dict[str, str]:
        return {"value": decimal_to_str(self.value), "unit": self.unit}

    @classmethod
    def from_dict(cls, data: Any, field: str = "value") -> Self:
        if not isinstance(data, dict) or set(data) != {"value", "unit"}:
            raise InvalidQuantity(details={"field": field, "reason": "not_a_quantity"})
        return cls.of(data["value"], data["unit"], field)

    def __str__(self) -> str:
        return f"{decimal_to_str(self.value)} {self.unit}"
