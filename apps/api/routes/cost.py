"""Cost analyses of an architecture: run (with scenarios), list, read, line items, and the cost
model catalog. Route handlers only translate HTTP to the service and back; the rules live in
core/domain/cost and engines/cost."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import CostServiceDep, RateLimitsDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.cost import (
    CostAnalysisPage,
    CostAnalysisResponse,
    CostAnalysisSummary,
    CostModelCatalog,
    CostModelModel,
    LineItemModel,
    LineItemPage,
    RunCostAnalysisRequest,
)
from core.domain.capacity.units import exact
from core.domain.cost.queries import LineItemQuery
from core.domain.cost.results import CostCategory, LineStatus

router = APIRouter(tags=["cost"])

_ANALYSES = "/projects/{project_id}/architectures/{architecture_id}/cost-analyses"
_ONE = _ANALYSES + "/{cost_analysis_id}"
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_not_found"},
}
READ_ONE = ERRORS | {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, architecture_not_found, cost_analysis_not_found",
    },
    422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"},
}


@router.post(
    _ANALYSES,
    status_code=status.HTTP_201_CREATED,
    response_model=CostAnalysisResponse,
    responses=ERRORS
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, architecture_revision_not_found, "
            "pricing_snapshot_not_found, capacity_analysis_not_found",
        },
        409: {"model": ErrorResponse, "description": "project_archived, architecture_archived"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_cost_request, incompatible_capacity_analysis, invalid_capacity_scenario, "
            "invalid_capacity_quantity, invalid_money (details: field, reason, …), validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Estimate an architecture's cost",
    description=(
        "Prices a revision (default: the current one) with one of the organization's pricing "
        "snapshots, optionally taking usage from a capacity analysis of the same revision and "
        "comparing scenarios, and stores the analysis. Synchronous and deterministic. An estimate, "
        "not an invoice: every amount traces to a price record; what cannot be priced is reported "
        "as unknown, never 0, and the known total is then a lower bound. Line items are read with "
        "GET …/line-items."
    ),
)
async def run_cost_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: RunCostAnalysisRequest,
    current: CurrentUser,
    cost: CostServiceDep,
    limits: RateLimitsDep,
) -> CostAnalysisResponse:
    await limits.enforce("run_cost_analysis", user_id=current.user.id)
    hours = body.operating_hours_per_month
    report = await cost.analyze(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        snapshot_id=body.snapshot_id,
        revision_number=body.revision,
        currency=body.currency,
        pricing_date=body.pricing_date,
        operating_hours_per_month=exact(hours, "operating_hours_per_month") if hours is not None else None,
        capacity_analysis_id=body.capacity_analysis_id,
        replicas_from_capacity=body.replicas_from_capacity,
        assumptions=tuple(a.to_domain() for a in body.assumptions),
        label=body.label,
        scenarios=tuple(s.to_cost_domain() for s in body.scenarios),
    )
    return CostAnalysisResponse.of(report)


@router.get(
    _ANALYSES,
    response_model=CostAnalysisPage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Cost analyses of an architecture",
    description="Newest first, optionally of one revision.",
)
async def list_cost_analyses(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    cost: CostServiceDep,
    revision: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CostAnalysisPage:
    page = await cost.list_analyses(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        revision=revision,
        cursor=cursor,
        limit=limit,
    )
    return CostAnalysisPage(
        analyses=[CostAnalysisSummary.of(r) for r in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _ONE,
    response_model=CostAnalysisResponse,
    responses=READ_ONE,
    summary="A cost analysis",
    description="Totals, breakdowns, unknown items, drivers, assumptions and scenario comparisons.",
)
async def get_cost_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    cost_analysis_id: uuid.UUID,
    current: CurrentUser,
    cost: CostServiceDep,
) -> CostAnalysisResponse:
    report = await cost.get(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=cost_analysis_id,
        user_id=current.user.id,
    )
    return CostAnalysisResponse.of(report)


@router.get(
    _ONE + "/line-items",
    response_model=LineItemPage,
    responses=READ_ONE,
    summary="Line items of a cost analysis",
    description="By component and resource: quantity, unit price, monthly amount and its price record.",
)
async def list_cost_line_items(  # noqa: PLR0913 -- one argument per filter
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    cost_analysis_id: uuid.UUID,
    current: CurrentUser,
    cost: CostServiceDep,
    *,
    component: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    status_filter: Annotated[LineStatus | None, Query(alias="status")] = None,
    category: CostCategory | None = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> LineItemPage:
    page = await cost.list_line_items(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=cost_analysis_id,
        user_id=current.user.id,
        query=LineItemQuery(component, status_filter, category, limit=limit),
        cursor=cursor,
    )
    return LineItemPage(
        line_items=[LineItemModel.of(line) for line in page.items], next_cursor=page.next_cursor
    )


@router.get(
    "/cost/models",
    response_model=CostModelCatalog,
    responses={401: {"model": ErrorResponse}},
    summary="The cost models",
    description="Every model the engine offers: what it prices and what it needs.",
)
async def list_cost_models(current: CurrentUser, cost: CostServiceDep) -> CostModelCatalog:
    return CostModelCatalog(models=[CostModelModel.model_validate(m) for m in cost.models()])
