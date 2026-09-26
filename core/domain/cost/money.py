"""Money and billing periods: exact decimal amounts in one currency, with explicit rounding.

**Currencies** are ISO 4217 codes (three capital letters; the code's format is checked, not its
membership in the ISO list). Amounts in different currencies are never combined: there is no
conversion (``CurrencyMismatch``).

**Precision.** Calculations use Python's ``Decimal`` (never binary floating point) in a local
context of 34 significant digits, rounding half-even. Amounts are kept to 12 decimal places
(``STORED_PLACES``) wherever they are stored or compared, so a unit price of 0.000000400 USD per
request times a billion requests stays exact. Rounding to the currency's minor unit (2 places for
USD and most currencies, 0 for JPY, 3 for BHD, …: ``MINOR_UNITS``) happens **once, for display**
(``Money.display``), never on intermediate values.

**Billing periods.** Conventions, documented and fixed: an hour, a day of 24 hours, a month of
730 hours (8,760 / 12, the convention most provider calculators use), a year of 8,760 hours (365
days). Months therefore all have the same length here; a calendar month has 672 to 744 hours.
"""

import decimal
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from core.domain.requirements.value_objects import decimal_to_str

from .errors import CurrencyMismatch, InvalidMoney

CURRENCY_FORMAT = re.compile(r"^[A-Z]{3}$")
STORED_PLACES = 12
STORED = Decimal(1).scaleb(-STORED_PLACES)
MAX_AMOUNT = Decimal(10) ** 15
# ISO 4217 minor units that are not 2 (all other currencies use 2).
MINOR_UNITS: dict[str, int] = {
    **dict.fromkeys(("JPY", "KRW", "VND", "CLP", "ISK", "PYG", "UGX", "XAF", "XOF", "XPF", "BIF", "DJF",
                     "GNF", "KMF", "RWF", "VUV"), 0),
    **dict.fromkeys(("BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"), 3),
}  # fmt: skip


@contextmanager
def arithmetic() -> Iterator[None]:
    """The context every monetary calculation runs in: 34 digits, half-even."""
    with decimal.localcontext() as context:
        context.prec = 34
        context.rounding = decimal.ROUND_HALF_EVEN
        yield


def currency(value: object, field: str = "currency") -> str:
    if not isinstance(value, str) or not CURRENCY_FORMAT.fullmatch(value):
        raise InvalidMoney(details={"field": field, "reason": "invalid_currency"})
    return value


def minor_units(code: str) -> int:
    return MINOR_UNITS.get(code, 2)


def stored(value: Decimal) -> Decimal:
    """``value`` at the stored precision (12 places, half-even), canonical (no -0, no exponent noise)."""
    with arithmetic():
        return value.quantize(STORED).normalize() + 0


def amount(raw: object, field: str = "amount", *, negative: bool = False) -> Decimal:
    """An exact, finite amount below 10^15, at most 12 decimal places; negative only when allowed
    (differences between costs can be negative, prices and costs cannot)."""
    if isinstance(raw, bool):
        raise InvalidMoney(details={"field": field, "reason": "not_a_number"})
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
                raise InvalidMoney(details={"field": field, "reason": "not_a_number"})
    except decimal.InvalidOperation:
        raise InvalidMoney(details={"field": field, "reason": "not_a_number"}) from None
    if not value.is_finite():
        raise InvalidMoney(details={"field": field, "reason": "not_a_number"})
    if value < 0 and not negative:
        raise InvalidMoney(details={"field": field, "reason": "negative"})
    if abs(value) >= MAX_AMOUNT:
        raise InvalidMoney(details={"field": field, "reason": "out_of_range"})
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -STORED_PLACES and value != value.quantize(STORED):
        raise InvalidMoney(details={"field": field, "reason": "too_precise"})
    return stored(value)


@dataclass(frozen=True, slots=True)
class Money:
    """An amount in a currency, at the stored precision. Differences may be negative."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", amount(self.amount, negative=True))
        currency(self.currency)

    @classmethod
    def of(cls, value: object, code: str) -> Self:
        return cls(amount(value, negative=True), currency(code))

    @classmethod
    def calculated(cls, value: Decimal, code: str) -> Self:
        """A calculated amount, brought to the stored precision (half-even)."""
        return cls(stored(value), currency(code))

    @classmethod
    def zero(cls, code: str) -> Self:
        return cls(Decimal(0), currency(code))

    def _same(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(details={"currencies": sorted({self.currency, other.currency})})

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money.calculated(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money.calculated(self.amount - other.amount, self.currency)

    def times(self, factor: Decimal) -> Money:
        with arithmetic():
            return Money.calculated(self.amount * factor, self.currency)

    @property
    def display(self) -> Decimal:
        """Rounded half-even to the currency's minor unit: for showing, never for calculating."""
        with arithmetic():
            return self.amount.quantize(Decimal(1).scaleb(-minor_units(self.currency))) + 0

    def to_dict(self) -> dict[str, str]:
        return {
            "amount": decimal_to_str(self.amount),
            "currency": self.currency,
            "display": f"{self.display:.{minor_units(self.currency)}f}",  # "12.50", "1235", "1.235"
        }

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        if not isinstance(data, dict) or not {"amount", "currency"} <= set(data):
            raise InvalidMoney(details={"field": "money", "reason": "not_money"})
        return cls.of(data["amount"], data["currency"])


def total(amounts: list[Money], code: str) -> Money:
    """The sum, all in ``code`` (anything else is a CurrencyMismatch)."""
    result = Money.zero(code)
    for item in amounts:
        result = result + item
    return result


class BillingPeriod(StrEnum):
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"

    @property
    def hours(self) -> Decimal:
        return PERIOD_HOURS[self]


HOURS_PER_MONTH = Decimal(730)
HOURS_PER_YEAR = Decimal(8760)
PERIOD_HOURS: dict[BillingPeriod, Decimal] = {
    BillingPeriod.HOUR: Decimal(1),
    BillingPeriod.DAY: Decimal(24),
    BillingPeriod.MONTH: HOURS_PER_MONTH,
    BillingPeriod.YEAR: HOURS_PER_YEAR,
}


def convert(value: Money, source: BillingPeriod, target: BillingPeriod) -> Money:
    """A recurring amount per ``source`` period expressed per ``target`` period (by hours)."""
    with arithmetic():
        return Money.calculated(value.amount * target.hours / source.hours, value.currency)
