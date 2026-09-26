"""An organization's pricing snapshots: immutable price lists the cost engine prices with. Route
handlers only translate HTTP to the service and back; the rules live in core/domain/cost."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.permissions import CurrentMembership, require_permission
from apps.api.dependencies.services import PricingServiceDep, RateLimitsDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.pricing import (
    CreatePricingSnapshotRequest,
    PricingRecordModel,
    PricingRecordPage,
    PricingSnapshotModel,
    PricingSnapshotPage,
)
from core.domain.cost.queries import RecordQuery
from core.domain.organizations.permissions import Permission

router = APIRouter(prefix="/organizations/{organization_id}/pricing-snapshots", tags=["pricing"])

ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "organization_not_found"},
}
ONE = ERRORS | {
    404: {"model": ErrorResponse, "description": "organization_not_found, pricing_snapshot_not_found"},
    422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"},
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=PricingSnapshotModel,
    responses=ERRORS
    | {
        413: {"model": ErrorResponse, "description": "payload_too_large (2 MiB)"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_pricing_record (details: field, reason), invalid_pricing_snapshot, "
            "invalid_money, validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    dependencies=[require_permission(Permission.PRICING_MANAGE)],
    summary="Create a pricing snapshot",
    description=(
        "Owners and admins. An immutable price list for the organization's cost analyses: every "
        "record states its provider, service, SKU, region, currency, unit, pricing model and price, "
        "effective date and source. ArchitectOS ships no prices; nothing is fetched from providers."
    ),
)
async def create_pricing_snapshot(
    body: CreatePricingSnapshotRequest,
    scoped: CurrentMembership,
    pricing: PricingServiceDep,
    limits: RateLimitsDep,
) -> PricingSnapshotModel:
    await limits.enforce("create_pricing_snapshot", user_id=scoped.membership.user_id)
    summary = await pricing.create(
        membership=scoped.membership,
        name=body.name,
        description=body.description,
        records=tuple(r.to_domain() for r in body.records),
    )
    return PricingSnapshotModel.of(summary)


@router.get(
    "",
    response_model=PricingSnapshotPage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    dependencies=[require_permission(Permission.ORGANIZATION_READ)],
    summary="Pricing snapshots of an organization",
    description="Newest first, without their records.",
)
async def list_pricing_snapshots(
    scoped: CurrentMembership,
    pricing: PricingServiceDep,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PricingSnapshotPage:
    page = await pricing.list(membership=scoped.membership, cursor=cursor, limit=limit)
    return PricingSnapshotPage(
        snapshots=[PricingSnapshotModel.of(s) for s in page.items], next_cursor=page.next_cursor
    )


@router.get(
    "/{snapshot_id}",
    response_model=PricingSnapshotModel,
    responses=ONE,
    dependencies=[require_permission(Permission.ORGANIZATION_READ)],
    summary="A pricing snapshot",
)
async def get_pricing_snapshot(
    snapshot_id: uuid.UUID, scoped: CurrentMembership, pricing: PricingServiceDep
) -> PricingSnapshotModel:
    return PricingSnapshotModel.of(await pricing.get(membership=scoped.membership, snapshot_id=snapshot_id))


@router.get(
    "/{snapshot_id}/records",
    response_model=PricingRecordPage,
    responses=ONE,
    dependencies=[require_permission(Permission.ORGANIZATION_READ)],
    summary="Records of a pricing snapshot",
    description="By record id, optionally of one provider, service or region; at most 500 per page.",
)
async def list_pricing_records(
    snapshot_id: uuid.UUID,
    scoped: CurrentMembership,
    pricing: PricingServiceDep,
    provider: Annotated[str | None, Query(max_length=64)] = None,
    service: Annotated[str | None, Query(max_length=64)] = None,
    region: Annotated[str | None, Query(max_length=63)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> PricingRecordPage:
    page = await pricing.records(
        membership=scoped.membership,
        snapshot_id=snapshot_id,
        query=RecordQuery(provider, service, region, limit=limit),
        cursor=cursor,
    )
    return PricingRecordPage(
        records=[PricingRecordModel.of(r) for r in page.items], next_cursor=page.next_cursor
    )
