"""Pricing records and snapshots: where every price comes from.

A **pricing record** is one price a provider (or the organization) charges: which provider,
service and SKU, in which region and currency, for which billing unit, under which pricing model,
effective from when, from which source, retrieved when. Nothing here is a default: a record states
all of it or is refused. Providers do not share SKU or billing semantics, so a record's identifiers
are compared exactly and never interpreted.

**Pricing models** (each with its calculation, in ``charge``):

- ``fixed``: a recurring ``unit_price`` per ``unit`` (``month`` or ``hour``), independent of usage;
- ``per_unit``: ``unit_price`` per ``per`` units of usage (e.g. 0.40 USD per 1,000,000 requests);
- ``tiered``: graduated tiers per billing month: each tier's price applies to the usage that falls
  within it (``up_to`` is the tier's upper bound in units, the last tier is open-ended).

A **pricing snapshot** is an immutable, content-hashed set of records belonging to one
organization. An analysis cites one snapshot, so the prices behind a historical result never
change; a new price list is a new snapshot.
"""

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from core.domain.requirements.value_objects import decimal_to_str
from core.domain.text import has_forbidden_characters

from .errors import InvalidMoney, InvalidPricingRecord, InvalidPricingSnapshot
from .money import amount, arithmetic, currency, stored

IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")  # provider, service, record id
SKU = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")  # e.g. "db.r6g.large", "AmazonS3:TimedStorage"
REGION = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")  # e.g. "eu-west-1", or "global"
MAX_CONDITIONS = 10
MAX_TIERS = 20
MAX_RECORDS = 10_000
MAX_TEXT = 500


class PricingUnit(StrEnum):
    """What a price is charged per. Sizes are decimal (1 GB = 10^9 bytes)."""

    MONTH = "month"  # a fixed recurring charge per month
    HOUR = "hour"  # a fixed recurring charge per hour
    INSTANCE_HOUR = "instance_hour"
    VCPU_HOUR = "vcpu_hour"
    GB_HOUR = "gb_hour"  # memory
    GB_MONTH = "gb_month"  # storage held
    GB = "gb"  # data transferred
    REQUEST = "request"
    OPERATION = "operation"
    EVENT = "event"  # messages


FIXED_UNITS = frozenset({PricingUnit.MONTH, PricingUnit.HOUR})


class PricingModel(StrEnum):
    FIXED = "fixed"
    PER_UNIT = "per_unit"
    TIERED = "tiered"


class PricingSource(StrEnum):
    USER_INPUT = "user_input"  # entered by a person of the organization
    PROVIDER_IMPORT = "provider_import"  # imported from a provider's price list
    CATALOG = "catalog"  # a curated catalog (none ships with ArchitectOS)


def _invalid(field: str, reason: str) -> InvalidPricingRecord:
    return InvalidPricingRecord(details={"field": field, "reason": reason})


def _price(value: object, field: str) -> Decimal:
    try:
        return amount(value, field)
    except InvalidMoney as error:
        raise _invalid(field, error.details["reason"]) from None


def _text(value: object, field: str, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_TEXT
        or has_forbidden_characters(value)
    ):
        raise _invalid(field, "invalid_text")
    return " ".join(value.split())


@dataclass(frozen=True, slots=True)
class Tier:
    """Usage up to ``up_to`` units (inclusive) is charged ``unit_price`` per ``per`` units; the last
    tier has no upper bound."""

    up_to: Decimal | None
    unit_price: Decimal

    def to_dict(self) -> dict[str, str | None]:
        return {
            "up_to": decimal_to_str(self.up_to) if self.up_to is not None else None,
            "unit_price": decimal_to_str(self.unit_price),
        }


@dataclass(frozen=True, slots=True)
class PricingRecord:
    id: str  # unique within its snapshot
    provider: str  # e.g. "aws"
    service: str  # e.g. "rds"
    sku: str  # the provider's identifier, compared exactly
    region: str  # e.g. "eu-west-1", or "global"
    currency: str
    unit: PricingUnit
    model: PricingModel
    effective_from: date
    source: PricingSource
    unit_price: Decimal | None = None  # fixed and per_unit
    per: Decimal = Decimal(1)  # the price is per this many units
    tiers: tuple[Tier, ...] = ()  # tiered
    effective_to: date | None = None  # exclusive; None: until replaced
    retrieved_at: datetime | None = None
    conditions: tuple[str, ...] = ()  # e.g. ("on_demand", "linux"): labels, compared exactly
    description: str | None = None

    def __post_init__(self) -> None:
        for field, pattern in (
            ("id", IDENTIFIER),
            ("provider", IDENTIFIER),
            ("service", IDENTIFIER),
            ("sku", SKU),
            ("region", REGION),
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise _invalid(field, "invalid_identifier")
        try:
            currency(self.currency)
        except InvalidMoney:
            raise _invalid("currency", "invalid_currency") from None
        for field, enum in (("unit", PricingUnit), ("model", PricingModel), ("source", PricingSource)):
            if not isinstance(getattr(self, field), enum):
                raise _invalid(field, f"unknown_{field}")
        self._check_price()
        self._check_dates()
        if not isinstance(self.conditions, tuple) or len(self.conditions) > MAX_CONDITIONS:
            raise _invalid("conditions", "too_many")
        if not all(isinstance(c, str) and IDENTIFIER.fullmatch(c) for c in self.conditions):
            raise _invalid("conditions", "invalid_identifier")
        object.__setattr__(self, "conditions", tuple(sorted(set(self.conditions))))
        object.__setattr__(self, "description", _text(self.description, "description", required=False))

    def _check_price(self) -> None:
        object.__setattr__(self, "per", _price(self.per, "per"))
        if self.per == 0:
            raise _invalid("per", "must_be_positive")
        if (self.model is PricingModel.FIXED) != (self.unit in FIXED_UNITS):
            raise _invalid("unit", "not_for_model")
        if self.model is PricingModel.TIERED:
            if self.unit_price is not None:
                raise _invalid("unit_price", "not_for_model")
            self._check_tiers()
            return
        if self.tiers:
            raise _invalid("tiers", "not_for_model")
        if self.unit_price is None:
            raise _invalid("unit_price", "required")
        object.__setattr__(self, "unit_price", _price(self.unit_price, "unit_price"))

    def _check_tiers(self) -> None:
        if not isinstance(self.tiers, tuple) or not 0 < len(self.tiers) <= MAX_TIERS:
            raise _invalid("tiers", "required")
        checked: list[Tier] = []
        previous = Decimal(0)
        for index, tier in enumerate(self.tiers):
            if not isinstance(tier, Tier):
                raise _invalid("tiers", "invalid_tier")
            last = index == len(self.tiers) - 1
            if (tier.up_to is None) != last:
                raise _invalid("tiers", "only_the_last_tier_is_open")
            up_to = _price(tier.up_to, "tiers.up_to") if tier.up_to is not None else None
            if up_to is not None and up_to <= previous:
                raise _invalid("tiers", "not_ascending")
            checked.append(Tier(up_to, _price(tier.unit_price, "tiers.unit_price")))
            previous = up_to if up_to is not None else previous
        object.__setattr__(self, "tiers", tuple(checked))

    def _check_dates(self) -> None:
        if not isinstance(self.effective_from, date) or isinstance(self.effective_from, datetime):
            raise _invalid("effective_from", "not_a_date")
        if self.effective_to is not None and (
            not isinstance(self.effective_to, date)
            or isinstance(self.effective_to, datetime)
            or self.effective_to <= self.effective_from
        ):
            raise _invalid("effective_to", "not_after_effective_from")
        if self.retrieved_at is not None and (
            not isinstance(self.retrieved_at, datetime) or self.retrieved_at.tzinfo is None
        ):
            raise _invalid("retrieved_at", "not_an_aware_datetime")

    # --- calculation -----------------------------------------------------------------------------

    def effective_on(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day < self.effective_to)

    def charge(self, quantity: Decimal) -> Decimal:
        """The price of ``quantity`` units (for ``fixed``: of ``quantity`` periods of ``unit``),
        exact to the stored precision."""
        if quantity < 0:
            raise _invalid("quantity", "negative")
        with arithmetic():
            if self.model is not PricingModel.TIERED:
                return stored(quantity / self.per * self.unit_price)  # type: ignore[operator]
            total, lower = Decimal(0), Decimal(0)
            for tier in self.tiers:
                upper = tier.up_to if tier.up_to is not None else quantity
                used = min(quantity, upper) - lower
                if used <= 0:
                    break
                total += used / self.per * tier.unit_price
                lower = upper
            return stored(total)

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        """What a lookup matches on (besides the date and conditions)."""
        return (self.provider, self.service, self.sku, self.region, self.currency)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "service": self.service,
            "sku": self.sku,
            "region": self.region,
            "currency": self.currency,
            "unit": self.unit.value,
            "model": self.model.value,
            "unit_price": decimal_to_str(self.unit_price) if self.unit_price is not None else None,
            "per": decimal_to_str(self.per),
            "tiers": [t.to_dict() for t in self.tiers],
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "source": self.source.value,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "conditions": list(self.conditions),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        known = {
            "id",
            "provider",
            "service",
            "sku",
            "region",
            "currency",
            "unit",
            "model",
            "unit_price",
            "per",
            "tiers",
            "effective_from",
            "effective_to",
            "source",
            "retrieved_at",
            "conditions",
            "description",
        }
        if not isinstance(data, Mapping):
            raise _invalid("record", "not_an_object")
        unknown = sorted(set(data) - known)
        if unknown:
            raise _invalid(unknown[0], "unknown_field")
        try:
            return cls(
                id=data.get("id"),  # type: ignore[arg-type]
                provider=data.get("provider"),  # type: ignore[arg-type]
                service=data.get("service"),  # type: ignore[arg-type]
                sku=data.get("sku"),  # type: ignore[arg-type]
                region=data.get("region"),  # type: ignore[arg-type]
                currency=data.get("currency"),  # type: ignore[arg-type]
                unit=_enum(PricingUnit, data.get("unit"), "unit"),
                model=_enum(PricingModel, data.get("model"), "model"),
                unit_price=data.get("unit_price"),
                per=data.get("per", 1),
                tiers=tuple(
                    Tier(t.get("up_to"), t.get("unit_price"))  # validated by the record
                    for t in _list(data.get("tiers"), "tiers")
                ),
                effective_from=_date(data.get("effective_from"), "effective_from"),  # type: ignore[arg-type]
                effective_to=_date(data.get("effective_to"), "effective_to"),
                source=_enum(PricingSource, data.get("source"), "source"),
                retrieved_at=_datetime(data.get("retrieved_at")),
                conditions=tuple(_list(data.get("conditions"), "conditions")),
                description=data.get("description"),
            )
        except TypeError, AttributeError:
            raise _invalid("record", "malformed") from None


def _enum[E: StrEnum](enum: type[E], value: object, field: str) -> E:
    try:
        return enum(str(value))
    except ValueError:
        raise _invalid(field, f"unknown_{field}") from None


def _list(value: object, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid(field, "not_a_list")
    return value


def _date(value: object, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise _invalid(field, "not_a_date") from None


def _datetime(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise _invalid("retrieved_at", "not_an_aware_datetime") from None


@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    """An organization's immutable price list. ``content_hash`` identifies its records exactly."""

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    records: tuple[PricingRecord, ...]
    created_at: datetime
    created_by_user_id: uuid.UUID | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 100:
            raise InvalidPricingSnapshot(details={"field": "name", "reason": "invalid_text"})
        if not isinstance(self.records, tuple) or not 0 < len(self.records) <= MAX_RECORDS:
            raise InvalidPricingSnapshot(details={"field": "records", "reason": "count"})
        ids = [r.id for r in self.records]
        if len(ids) != len(set(ids)):
            raise InvalidPricingSnapshot(details={"field": "records", "reason": "duplicate_id"})
        object.__setattr__(
            self, "records", tuple(sorted(self.records, key=lambda r: (*r.key, r.effective_from, r.id)))
        )

    @property
    def content_hash(self) -> str:
        return content_hash(self.records)

    def record(self, record_id: str) -> PricingRecord | None:
        return next((r for r in self.records if r.id == record_id), None)


def content_hash(records: tuple[PricingRecord, ...]) -> str:
    """SHA-256 of the records' canonical JSON, whatever their order."""
    document = sorted((r.to_dict() for r in records), key=lambda r: r["id"])
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
