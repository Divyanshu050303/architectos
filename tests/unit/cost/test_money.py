"""Money and billing periods (Milestone 8, phase 1): exact decimals, one currency, one rounding."""

from decimal import Decimal

import pytest

from core.domain.cost.errors import CurrencyMismatch, InvalidMoney
from core.domain.cost.money import BillingPeriod, Money, convert, minor_units, total


def test_amounts_are_exact_and_rounded_only_for_display() -> None:
    per_request = Money.of("0.0000004", "USD")
    billion = per_request.times(Decimal(1_000_000_000))
    assert billion.amount == Decimal(400)
    third = Money.of(1, "USD").times(Decimal(1) / Decimal(3))
    assert third.amount == Decimal("0.333333333333")  # 12 places kept
    assert third.display == Decimal("0.33")
    assert Money.of("2.345", "USD").display == Decimal("2.34")  # half-even
    assert Money.of("2.355", "USD").display == Decimal("2.36")
    assert Money.of("1234.5", "JPY").display == Decimal(1234)  # no minor unit
    assert Money.of("1.2345", "BHD").display == Decimal("1.234")
    assert (minor_units("USD"), minor_units("JPY"), minor_units("KWD")) == (2, 0, 3)


def test_currencies_are_never_mixed() -> None:
    with pytest.raises(CurrencyMismatch):
        Money.of(1, "USD") + Money.of(1, "EUR")
    with pytest.raises(CurrencyMismatch):
        total([Money.of(1, "USD"), Money.of(1, "EUR")], "USD")
    assert total([Money.of("1.1", "EUR"), Money.of("2.2", "EUR")], "EUR") == Money.of("3.3", "EUR")
    assert (Money.of(1, "USD") - Money.of(3, "USD")).amount == Decimal(-2)  # differences may be negative


@pytest.mark.parametrize(
    ("value", "code", "reason"),
    [
        ("1", "usd", "invalid_currency"),
        ("1", "US", "invalid_currency"),
        ("1", "", "invalid_currency"),
        ("NaN", "USD", "not_a_number"),
        ("Infinity", "USD", "not_a_number"),
        (True, "USD", "not_a_number"),
        ("1e15", "USD", "out_of_range"),
        ("0.0000000000001", "USD", "too_precise"),
    ],
)
def test_invalid_amounts_and_currencies_are_refused(value: object, code: str, reason: str) -> None:
    with pytest.raises(InvalidMoney) as raised:
        Money.of(value, code)
    assert raised.value.details["reason"] == reason


def test_billing_periods_follow_the_documented_convention() -> None:
    hourly = Money.of("0.10", "USD")
    assert convert(hourly, BillingPeriod.HOUR, BillingPeriod.MONTH).amount == Decimal(73)  # 730 h
    assert convert(hourly, BillingPeriod.HOUR, BillingPeriod.YEAR).amount == Decimal(876)  # 8760 h
    assert convert(hourly, BillingPeriod.HOUR, BillingPeriod.DAY).amount == Decimal("2.4")
    monthly = Money.of(73, "USD")
    assert convert(monthly, BillingPeriod.MONTH, BillingPeriod.YEAR).amount == Decimal(876)  # x 12
    assert convert(monthly, BillingPeriod.MONTH, BillingPeriod.HOUR).amount == Decimal("0.1")


def test_serialization_is_explicit() -> None:
    money = Money.of("12.5", "EUR")
    assert money.to_dict() == {"amount": "12.5", "currency": "EUR", "display": "12.50"}
    assert Money.from_dict(money.to_dict()) == money
    with pytest.raises(InvalidMoney):
        Money.from_dict({"amount": "1"})
