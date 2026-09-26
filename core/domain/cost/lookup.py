"""Price lookup: exact, deterministic, and explicit about everything that is not an exact match.

A query names the provider, service and SKU, the region and currency, the day the price must be
effective on, the conditions the price must carry (e.g. ``on_demand``) and, when known, the unit the
calculation needs. The steps, in order, each ending the lookup when nothing is left:

1. records of the same provider, service and SKU (else ``not_found``);
2. … in the same region (else ``region_mismatch``: the regions that have it are listed, never used);
3. … in the same currency (else ``currency_mismatch``: never converted);
4. … effective on the day (else ``not_effective``);
5. … carrying every requested condition (else ``not_found``, naming the conditions);
6. the **latest effective** record wins (the most recent price in force on the day); two records
   with the same latest effective date are ``ambiguous``: none is picked;
7. the winner's unit must be the unit the calculation needs (else ``unit_mismatch``).

**Freshness** is reported with every price found: when it took effect, when it was retrieved, and
whether it is ``stale`` — retrieved more than ``max_age_days`` (default 90) before the pricing day,
or retrieved at an unknown time. A stale price is used, and flagged: it is never presented as
current.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from .pricing import PricingRecord, PricingSnapshot, PricingUnit

DEFAULT_MAX_AGE_DAYS = 90


class LookupOutcome(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    REGION_MISMATCH = "region_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    NOT_EFFECTIVE = "not_effective"
    AMBIGUOUS = "ambiguous"
    UNIT_MISMATCH = "unit_mismatch"


@dataclass(frozen=True, slots=True)
class PriceQuery:
    provider: str
    service: str
    sku: str
    region: str
    currency: str
    on: date
    conditions: frozenset[str] = frozenset()
    unit: PricingUnit | None = None

    def describe(self) -> str:
        wanted = f"{self.provider}/{self.service}/{self.sku} in {self.region} ({self.currency})"
        return wanted + (f" with {', '.join(sorted(self.conditions))}" if self.conditions else "")


@dataclass(frozen=True, slots=True)
class Freshness:
    effective_from: date
    retrieved_at: datetime | None
    age_days: int | None  # days between retrieval and the pricing day; None when unknown
    stale: bool

    @classmethod
    def of(cls, record: PricingRecord, on: date, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> Freshness:
        if record.retrieved_at is None:
            return cls(record.effective_from, None, None, stale=True)
        age = (on - record.retrieved_at.date()).days
        return cls(record.effective_from, record.retrieved_at, age, stale=age > max_age_days)


@dataclass(frozen=True, slots=True)
class Lookup:
    outcome: LookupOutcome
    query: PriceQuery
    record: PricingRecord | None = None
    freshness: Freshness | None = None
    candidates: tuple[str, ...] = ()  # record ids that were considered at the failing step
    detail: str = ""  # e.g. the regions or currencies that do have the SKU
    missing: tuple[str, ...] = field(default=())

    @property
    def found(self) -> bool:
        return self.outcome is LookupOutcome.FOUND

    @property
    def message(self) -> str:  # noqa: PLR0911 -- one sentence per outcome
        wanted = self.query.describe()
        match self.outcome:
            case LookupOutcome.FOUND:
                return f"Priced by record {self.record.id if self.record else ''}."
            case LookupOutcome.NOT_FOUND:
                return f"The snapshot has no price for {wanted}."
            case LookupOutcome.REGION_MISMATCH:
                return f"No price for {wanted}; the snapshot prices it only in {self.detail}."
            case LookupOutcome.CURRENCY_MISMATCH:
                return f"No price for {wanted}; the snapshot prices it only in {self.detail} (no conversion)."
            case LookupOutcome.NOT_EFFECTIVE:
                return f"No price for {wanted} is effective on {self.query.on.isoformat()}."
            case LookupOutcome.AMBIGUOUS:
                return (
                    f"Several prices match {wanted} equally ({', '.join(self.candidates)}); none is chosen."
                )
            case LookupOutcome.UNIT_MISMATCH:
                return f"The price for {wanted} is per {self.detail}, not the unit this cost needs."


def lookup(  # noqa: PLR0911 -- one documented step per return
    snapshot: PricingSnapshot, query: PriceQuery, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS
) -> Lookup:
    same = [
        r
        for r in snapshot.records
        if (r.provider, r.service, r.sku) == (query.provider, query.service, query.sku)
    ]
    if not same:
        return Lookup(LookupOutcome.NOT_FOUND, query, missing=("price",))
    here = [r for r in same if r.region == query.region]
    if not here:
        regions = ", ".join(sorted({r.region for r in same}))
        return Lookup(
            LookupOutcome.REGION_MISMATCH,
            query,
            candidates=_ids(same),
            detail=regions,
            missing=("price.region",),
        )
    priced = [r for r in here if r.currency == query.currency]
    if not priced:
        currencies = ", ".join(sorted({r.currency for r in here}))
        return Lookup(
            LookupOutcome.CURRENCY_MISMATCH,
            query,
            candidates=_ids(here),
            detail=currencies,
            missing=("price.currency",),
        )
    effective = [r for r in priced if r.effective_on(query.on)]
    if not effective:
        return Lookup(
            LookupOutcome.NOT_EFFECTIVE, query, candidates=_ids(priced), missing=("price.effective_date",)
        )
    matching = [r for r in effective if query.conditions <= set(r.conditions)]
    if not matching:
        return Lookup(
            LookupOutcome.NOT_FOUND, query, candidates=_ids(effective), missing=("price.conditions",)
        )
    latest = max(r.effective_from for r in matching)
    winners = [r for r in matching if r.effective_from == latest]
    if len(winners) > 1:
        return Lookup(
            LookupOutcome.AMBIGUOUS, query, candidates=_ids(winners), missing=("price.unambiguous",)
        )
    [record] = winners
    if query.unit is not None and record.unit is not query.unit:
        return Lookup(
            LookupOutcome.UNIT_MISMATCH,
            query,
            record,
            candidates=(record.id,),
            detail=record.unit.value,
            missing=("price.unit",),
        )
    return Lookup(LookupOutcome.FOUND, query, record, Freshness.of(record, query.on, max_age_days))


def _ids(records: list[PricingRecord]) -> tuple[str, ...]:
    return tuple(sorted(r.id for r in records))
