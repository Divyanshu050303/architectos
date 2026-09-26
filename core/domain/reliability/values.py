"""Reliability quantities: availability as an exact fraction, durations in explicit units.

Availability is a fraction of time a component or path is able to serve (0.999 = 99.9 %), never a
percentage number, in ``[0, 1]``. Calculations run in the shared exact context (34 digits,
half-even) and are kept, like every capacity quantity, to 9 decimal places (``ratio``): 10^-9 of a
730-hour month is 2.6 ms of downtime. Durations are quantities of the duration dimension (``ms``,
``s``, ``min``, ``h``, ``d``), calculated in seconds.

Nothing here has a default: an availability or duration that is not declared is unknown (``None``),
never 0 or 1.
"""

from decimal import Decimal

from core.domain.capacity.errors import InvalidQuantity
from core.domain.capacity.units import Dimension, Quantity, exact
from core.domain.numbers import arithmetic

from .errors import InvalidReliabilityRequest

RATIO = "ratio"
SECONDS = "s"
HOURS_PER_MONTH = Decimal(730)  # the billing convention of the cost engine: 8760 / 12
MINUTES_PER_MONTH = HOURS_PER_MONTH * 60


def _invalid(field: str, reason: str) -> InvalidReliabilityRequest:
    return InvalidReliabilityRequest(details={"field": field, "reason": reason})


def fraction(raw: object, field: str) -> Decimal:
    """An availability: an exact decimal in [0, 1]."""
    try:
        value = exact(raw, field)
    except InvalidQuantity as error:
        raise _invalid(field, error.details["reason"]) from None
    if not 0 <= value <= 1:
        raise _invalid(field, "out_of_range")
    return value


def availability(value: Decimal) -> Quantity:
    """A calculated availability, kept to 9 places."""
    with arithmetic():
        return Quantity.rounded(value, RATIO)


def seconds(value: Decimal) -> Quantity:
    with arithmetic():
        return Quantity.rounded(value, SECONDS)


def in_seconds(duration: Quantity, field: str) -> Decimal:
    """A duration quantity in seconds, exactly."""
    if duration.dimension is not Dimension.DURATION:
        raise _invalid(field, "not_a_duration")
    with arithmetic():
        return duration.canonical / 1000  # canonical: milliseconds


def downtime_minutes_per_month(value: Decimal) -> Decimal:
    """(1 - availability) x 730 h x 60: what an availability allows, not what will happen."""
    with arithmetic():
        return (1 - value) * MINUTES_PER_MONTH
