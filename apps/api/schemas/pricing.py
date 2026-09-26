"""Pricing snapshots over HTTP: an organization's immutable price lists, record by record."""

import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import AwareDatetime, Field, StrictInt

from core.domain.cost.money import amount
from core.domain.cost.pricing import (
    MAX_CONDITIONS,
    MAX_RECORDS,
    MAX_TIERS,
    PricingModel,
    PricingRecord,
    PricingSource,
    PricingUnit,
    Tier,
)
from core.domain.cost.queries import SnapshotSummary

from .common import ApiModel, RequestModel

type Decimalish = Annotated[str, Field(max_length=40)] | StrictInt | float


class TierModel(ApiModel):
    up_to: str | None = Field(description="Upper bound of the tier in units; null for the last tier.")
    unit_price: str


class TierInput(RequestModel):
    up_to: Decimalish | None = None
    unit_price: Decimalish


class PricingRecordInput(RequestModel):
    """One price, stated in full: nothing is defaulted except ``per`` (1)."""

    id: Annotated[str, Field(min_length=1, max_length=64, examples=["rds-r6g-large-euw1"])]
    provider: Annotated[str, Field(min_length=1, max_length=64, examples=["aws"])]
    service: Annotated[str, Field(min_length=1, max_length=64, examples=["rds"])]
    sku: Annotated[str, Field(min_length=1, max_length=128, examples=["db.r6g.large"])]
    region: Annotated[str, Field(min_length=1, max_length=63, examples=["eu-west-1"])]
    currency: Annotated[str, Field(min_length=3, max_length=3, examples=["USD"])]
    unit: PricingUnit
    model: PricingModel
    unit_price: Decimalish | None = None
    per: Decimalish = 1
    tiers: Annotated[list[TierInput], Field(max_length=MAX_TIERS)] = Field(default_factory=list)
    effective_from: date
    effective_to: date | None = None
    source: PricingSource
    retrieved_at: AwareDatetime | None = None
    conditions: Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=MAX_CONDITIONS)] = (
        Field(default_factory=list)
    )
    description: Annotated[str | None, Field(max_length=500)] = None

    def to_domain(self) -> PricingRecord:
        return PricingRecord(
            id=self.id,
            provider=self.provider,
            service=self.service,
            sku=self.sku,
            region=self.region,
            currency=self.currency,
            unit=self.unit,
            model=self.model,
            unit_price=amount(self.unit_price, "unit_price") if self.unit_price is not None else None,
            per=amount(self.per, "per"),
            tiers=tuple(
                Tier(
                    amount(t.up_to, "tiers.up_to") if t.up_to is not None else None,
                    amount(t.unit_price, "tiers.unit_price"),
                )
                for t in self.tiers
            ),
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            source=self.source,
            retrieved_at=self.retrieved_at,
            conditions=tuple(self.conditions),
            description=self.description,
        )


class CreatePricingSnapshotRequest(RequestModel):
    name: Annotated[
        str, Field(min_length=1, max_length=100, examples=["AWS eu-west-1 list prices, Sep 2026"])
    ]
    description: Annotated[str | None, Field(max_length=500)] = None
    records: Annotated[list[PricingRecordInput], Field(min_length=1, max_length=MAX_RECORDS)]


class PricingRecordModel(ApiModel):
    id: str
    provider: str
    service: str
    sku: str
    region: str
    currency: str
    unit: PricingUnit
    model: PricingModel
    unit_price: str | None = Field(description="Exact decimal, per `per` units; null for tiered prices.")
    per: str
    tiers: list[TierModel]
    effective_from: date
    effective_to: date | None
    source: PricingSource
    retrieved_at: datetime | None
    conditions: list[str]
    description: str | None

    @classmethod
    def of(cls, record: PricingRecord) -> PricingRecordModel:
        return cls.model_validate(record.to_dict())


class PricingSnapshotModel(ApiModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None
    record_count: int
    content_hash: str = Field(description="SHA-256 of the records: identifies the price list exactly.")
    created_at: datetime
    created_by_user_id: uuid.UUID | None

    @classmethod
    def of(cls, summary: SnapshotSummary) -> PricingSnapshotModel:
        return cls(
            id=summary.id,
            organization_id=summary.organization_id,
            name=summary.name,
            description=summary.description,
            record_count=summary.record_count,
            content_hash=summary.content_hash,
            created_at=summary.created_at,
            created_by_user_id=summary.created_by_user_id,
        )


class PricingSnapshotPage(ApiModel):
    snapshots: list[PricingSnapshotModel]
    next_cursor: str | None


class PricingRecordPage(ApiModel):
    records: list[PricingRecordModel]
    next_cursor: str | None
