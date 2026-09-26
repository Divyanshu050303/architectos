"""What a cost analysis produces: line items with their prices' provenance, and results that never
turn an unknown cost into zero.

A **line item** is one billed resource of one component (instance hours of the API, storage of the
database, requests to the gateway): quantity per month in the price's unit, the unit price, the
monthly amount, and the pricing record it came from (snapshot, provider, service, SKU, region,
effective date, source, retrieval time), the model that calculated it and the assumptions it used.
A line whose quantity, price or mapping is not available is ``unknown``: it has no amount (never
0) and names what it misses.

The **result** keeps the known subtotal apart from the unknown items: ``totals.complete`` is true
only when no item is unknown and nothing was unsupported. Every amount is in the analysis's single
currency; amounts are monthly (730 hours) and converted by billing period for hourly and annual
views. Estimated cost is not an invoice: nothing here is observed spending.
"""

import hashlib
import json
import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from core.domain.engine_results import (
    Evidence,
    Limitation,
    ModelSet,
    Unsupported,
    evidence_problem,
    ids_problem,
    read_evidence,
    text_problem,
)
from core.domain.requirements.value_objects import decimal_to_str

from .errors import InvalidCostResult
from .money import BillingPeriod, Money, convert, stored
from .pricing import IDENTIFIER, PricingModel, PricingRecord, PricingSource, PricingUnit

MODEL_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
MAX_ID = 256


class CostCategory(StrEnum):
    COMPUTE = "compute"
    DATABASE = "database"
    STORAGE = "storage"
    NETWORK = "network"
    MANAGED_SERVICE = "managed_service"
    MESSAGING = "messaging"
    OBSERVABILITY = "observability"
    OTHER = "other"


class CostKind(StrEnum):
    FIXED = "fixed"  # independent of usage (a recurring charge, instance hours)
    USAGE = "usage"  # grows with usage (requests, transfer, storage consumed)


class LineStatus(StrEnum):
    PRICED = "priced"
    UNKNOWN = "unknown"


class CostStatus(StrEnum):
    COMPLETED = "completed"  # every line priced, nothing unsupported
    PARTIAL = "partial"  # some lines priced, others unknown or unsupported
    INSUFFICIENT_PRICING = "insufficient_pricing"  # lines exist, none could be priced
    UNSUPPORTED = "unsupported"  # nothing a cost model applies to
    FAILED = "failed"  # the analysis could not run


def _check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidCostResult(details={"fields": found})


@dataclass(frozen=True, slots=True)
class PriceRef:
    """The provenance of the price a line used: which record of which snapshot."""

    snapshot_id: uuid.UUID
    record_id: str
    provider: str
    service: str
    sku: str
    region: str
    currency: str
    unit: PricingUnit
    model: PricingModel
    effective_from: date
    effective_to: date | None
    source: PricingSource
    retrieved_at: datetime | None

    @classmethod
    def of(cls, snapshot_id: uuid.UUID, record: PricingRecord) -> Self:
        return cls(
            snapshot_id,
            record.id,
            record.provider,
            record.service,
            record.sku,
            record.region,
            record.currency,
            record.unit,
            record.model,
            record.effective_from,
            record.effective_to,
            record.source,
            record.retrieved_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": str(self.snapshot_id),
            "record_id": self.record_id,
            "provider": self.provider,
            "service": self.service,
            "sku": self.sku,
            "region": self.region,
            "currency": self.currency,
            "unit": self.unit.value,
            "model": self.model.value,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "source": self.source.value,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        return cls(
            uuid.UUID(data["snapshot_id"]),
            data["record_id"],
            data["provider"],
            data["service"],
            data["sku"],
            data["region"],
            data["currency"],
            PricingUnit(data["unit"]),
            PricingModel(data["model"]),
            date.fromisoformat(data["effective_from"]),
            date.fromisoformat(data["effective_to"]) if data.get("effective_to") else None,
            PricingSource(data["source"]),
            datetime.fromisoformat(data["retrieved_at"]) if data.get("retrieved_at") else None,
        )


@dataclass(frozen=True, slots=True)
class LineItem:
    element_id: str  # the node billed
    resource: str  # e.g. "instance_hours", "storage", "requests"
    category: CostCategory
    kind: CostKind
    status: LineStatus
    quantity: Decimal | None = None  # per month, in ``unit``
    unit: PricingUnit | None = None
    unit_price: Decimal | None = None  # per ``per`` units (tiered: None)
    per: Decimal | None = None
    monthly: Money | None = None
    price: PriceRef | None = None
    model_id: str | None = None
    model_version: int | None = None
    assumptions: tuple[Evidence, ...] = ()
    missing: tuple[str, ...] = ()  # what an unknown line lacks
    reason: str | None = None  # why an unknown line is unknown

    def __post_init__(self) -> None:
        if isinstance(self.missing, tuple):
            object.__setattr__(self, "missing", tuple(sorted(set(self.missing))))
        priced = self.status is LineStatus.PRICED
        complete = self.monthly is not None and self.price is not None and self.quantity is not None
        _check(
            [
                None
                if isinstance(self.element_id, str) and 0 < len(self.element_id) <= MAX_ID
                else "element_id",
                None
                if isinstance(self.resource, str) and IDENTIFIER.fullmatch(self.resource)
                else "resource",
                None if isinstance(self.category, CostCategory) else "category",
                None if isinstance(self.kind, CostKind) else "kind",
                None if isinstance(self.status, LineStatus) else "status",
                None if not priced or complete else "monthly",
                None if priced or self.monthly is None else "monthly",  # unknown is never an amount
                None if priced or self.missing else "missing",
                None if self.quantity is None or self.quantity >= 0 else "quantity",
                None if self.monthly is None or self.monthly.amount >= 0 else "monthly",
                None
                if self.monthly is None or self.price is None or self.monthly.currency == self.price.currency
                else "currency",
                None if self.model_id is None or MODEL_ID.fullmatch(self.model_id) else "model_id",
                None if (self.model_id is None) == (self.model_version is None) else "model_version",
                evidence_problem(self.assumptions),
                ids_problem(self.missing, "missing"),
                text_problem(self.reason, "reason", required=False),
            ]
        )

    @property
    def priced(self) -> bool:
        return self.status is LineStatus.PRICED

    def sort_key(self) -> tuple[str, str, str]:
        return (self.element_id, self.resource, self.category.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "resource": self.resource,
            "category": self.category.value,
            "kind": self.kind.value,
            "status": self.status.value,
            "quantity": decimal_to_str(self.quantity) if self.quantity is not None else None,
            "unit": self.unit.value if self.unit else None,
            "unit_price": decimal_to_str(self.unit_price) if self.unit_price is not None else None,
            "per": decimal_to_str(self.per) if self.per is not None else None,
            "monthly": self.monthly.to_dict() if self.monthly else None,
            "price": self.price.to_dict() if self.price else None,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "assumptions": [e.to_dict() for e in self.assumptions],
            "missing": list(self.missing),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                element_id=data["element_id"],
                resource=data["resource"],
                category=CostCategory(data["category"]),
                kind=CostKind(data["kind"]),
                status=LineStatus(data["status"]),
                quantity=Decimal(data["quantity"]) if data.get("quantity") is not None else None,
                unit=PricingUnit(data["unit"]) if data.get("unit") else None,
                unit_price=Decimal(data["unit_price"]) if data.get("unit_price") is not None else None,
                per=Decimal(data["per"]) if data.get("per") is not None else None,
                monthly=Money.from_dict(data["monthly"]) if data.get("monthly") else None,
                price=PriceRef.from_dict(data["price"]) if data.get("price") else None,
                model_id=data.get("model_id"),
                model_version=data.get("model_version"),
                assumptions=read_evidence(data.get("assumptions")),
                missing=tuple(data.get("missing") or ()),
                reason=data.get("reason"),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidCostResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class Totals:
    """The known cost per period, and how much is not known. ``complete`` is true only when no line
    is unknown and nothing was unsupported: otherwise the known amounts are a lower bound."""

    currency: str
    monthly: Money
    unknown_items: int
    unsupported: int

    @property
    def complete(self) -> bool:
        return self.unknown_items == 0 and self.unsupported == 0

    @property
    def hourly(self) -> Money:
        return convert(self.monthly, BillingPeriod.MONTH, BillingPeriod.HOUR)

    @property
    def annual(self) -> Money:
        return convert(self.monthly, BillingPeriod.MONTH, BillingPeriod.YEAR)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Totals:
        try:
            return cls(
                data["currency"], Money.from_dict(data["monthly"]), data["unknown_items"], data["unsupported"]
            )
        except (KeyError, TypeError) as error:
            raise InvalidCostResult(details={"fields": [type(error).__name__]}) from None

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "hourly": self.hourly.to_dict(),
            "monthly": self.monthly.to_dict(),
            "annual": self.annual.to_dict(),
            "unknown_items": self.unknown_items,
            "unsupported": self.unsupported,
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class Share:
    """The known amount of a group (a component, a category, a provider, …), its share of the known
    total, and how many of its lines are priced or unknown. ``key`` is None for the lines that have
    no value for the grouping (e.g. the provider of a line no price was found for)."""

    key: str | None
    monthly: Money
    share: Decimal | None  # of the known monthly total; None when that total is 0
    priced_items: int
    unknown_items: int

    @property
    def complete(self) -> bool:
        return self.unknown_items == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "hourly": convert(self.monthly, BillingPeriod.MONTH, BillingPeriod.HOUR).to_dict(),
            "monthly": self.monthly.to_dict(),
            "annual": convert(self.monthly, BillingPeriod.MONTH, BillingPeriod.YEAR).to_dict(),
            "share": decimal_to_str(self.share) if self.share is not None else None,
            "priced_items": self.priced_items,
            "unknown_items": self.unknown_items,
            "complete": self.complete,
        }


def breakdown(
    lines: Iterable[LineItem], key: Callable[[LineItem], str | None], currency: str
) -> tuple[Share, ...]:
    """Known monthly amounts per ``key(line)``, largest first (ties by key, None last), with priced
    and unknown counts. Every amount must be in ``currency``: another currency is refused
    (``CurrencyMismatch``), never combined. The groups' amounts add up exactly to the known total."""
    known: dict[str | None, Money] = {}
    priced: dict[str | None, int] = defaultdict(int)
    unknown: dict[str | None, int] = defaultdict(int)
    for line in lines:
        name = key(line)
        total = known.setdefault(name, Money.zero(currency))
        if line.monthly is not None:
            known[name] = total + line.monthly  # CurrencyMismatch for another currency
            priced[name] += 1
        else:
            unknown[name] += 1
    grand = sum((m.amount for m in known.values()), Decimal(0))
    shares = [
        Share(name, money, stored(money.amount / grand) if grand else None, priced[name], unknown[name])
        for name, money in known.items()
    ]
    return tuple(sorted(shares, key=lambda s: (-s.monthly.amount, s.key is None, s.key or "")))


@dataclass(frozen=True, slots=True)
class CostResult:
    currency: str
    snapshot_id: uuid.UUID
    snapshot_hash: str
    model_set: ModelSet
    context_fingerprint: str
    line_items: tuple[LineItem, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "line_items", tuple(sorted(self.line_items, key=LineItem.sort_key)))
        object.__setattr__(
            self, "unsupported", tuple(sorted(set(self.unsupported), key=lambda u: (u.element_id, u.code)))
        )
        object.__setattr__(self, "limitations", tuple(sorted(set(self.limitations), key=lambda x: x.code)))
        keys = [(line.element_id, line.resource) for line in self.line_items]
        _check(
            [
                None if len(keys) == len(set(keys)) else "line_items",
                None
                if all(
                    line.monthly is None or line.monthly.currency == self.currency for line in self.line_items
                )
                else "currency",
            ]
        )

    @property
    def status(self) -> CostStatus:
        priced = sum(line.priced for line in self.line_items)
        unknown = len(self.line_items) - priced
        if priced and not unknown and not self.unsupported:
            return CostStatus.COMPLETED
        if priced:
            return CostStatus.PARTIAL
        if unknown:
            return CostStatus.INSUFFICIENT_PRICING
        return CostStatus.UNSUPPORTED

    @property
    def totals(self) -> Totals:
        known = [line.monthly for line in self.line_items if line.monthly is not None]
        monthly = Money.zero(self.currency)
        for amount in known:
            monthly = monthly + amount
        unknown = sum(not line.priced for line in self.line_items)
        return Totals(self.currency, monthly, unknown, len(self.unsupported))

    def by_component(self) -> tuple[Share, ...]:
        return breakdown(self.line_items, lambda line: line.element_id, self.currency)

    def by_category(self) -> tuple[Share, ...]:
        return breakdown(self.line_items, lambda line: line.category.value, self.currency)

    def by_kind(self) -> tuple[Share, ...]:
        return breakdown(self.line_items, lambda line: line.kind.value, self.currency)

    def by_resource(self) -> tuple[Share, ...]:
        return breakdown(self.line_items, lambda line: line.resource, self.currency)

    def by_provider(self) -> tuple[Share, ...]:
        return breakdown(
            self.line_items, lambda line: line.price.provider if line.price else None, self.currency
        )

    def by_region(self) -> tuple[Share, ...]:
        return breakdown(
            self.line_items, lambda line: line.price.region if line.price else None, self.currency
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "snapshot_id": str(self.snapshot_id),
            "snapshot_hash": self.snapshot_hash,
            "model_set": self.model_set.to_dict(),
            "context_fingerprint": self.context_fingerprint,
            "status": self.status.value,
            "totals": self.totals.to_dict(),
            "line_items": [line.to_dict() for line in self.line_items],
            "unsupported": [u.to_dict() for u in self.unsupported],
            "limitations": [x.to_dict() for x in self.limitations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CostResult:
        try:
            return cls(
                data["currency"],
                uuid.UUID(data["snapshot_id"]),
                data["snapshot_hash"],
                ModelSet.from_dict(data["model_set"]),
                data["context_fingerprint"],
                tuple(LineItem.from_dict(line) for line in data.get("line_items") or ()),
                tuple(Unsupported.from_dict(u) for u in data.get("unsupported") or ()),
                tuple(Limitation.from_dict(x) for x in data.get("limitations") or ()),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidCostResult(details={"fields": [type(error).__name__]}) from None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
