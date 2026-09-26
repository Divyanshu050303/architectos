"""Capacity analyses of an architecture: run (with scenarios), list, read, components, bottleneck
candidates, scenario comparisons, and the model catalog. Route handlers only translate HTTP to the
service and back; the rules live in core/domain/capacity and engines/capacity."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import CapacityServiceDep, RateLimitsDep
from apps.api.schemas.capacity import (
    BottleneckList,
    BottleneckModel,
    CapacityAnalysisPage,
    CapacityAnalysisResponse,
    CapacityAnalysisSummary,
    CapacityModelCatalog,
    CapacityModelModel,
    ComponentModel,
    ComponentPage,
    RunCapacityAnalysisRequest,
    ScenarioList,
    scenario_model,
)
from apps.api.schemas.common import ErrorResponse
from core.domain.capacity.queries import BottleneckQuery, ComponentQuery
from core.domain.capacity.results import Certainty, ComponentStatus

router = APIRouter(tags=["capacity"])

_ANALYSES = "/projects/{project_id}/architectures/{architecture_id}/capacity-analyses"
_ONE = _ANALYSES + "/{capacity_analysis_id}"
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_not_found"},
}
READ_ONE = ERRORS | {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, architecture_not_found, capacity_analysis_not_found",
    },
    422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"},
}


@router.post(
    _ANALYSES,
    status_code=status.HTTP_201_CREATED,
    response_model=CapacityAnalysisResponse,
    responses=ERRORS
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, architecture_revision_not_found",
        },
        409: {"model": ErrorResponse, "description": "project_archived, architecture_archived"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_workload_profile, invalid_capacity_quantity, invalid_capacity_config, "
            "invalid_capacity_scenario (details: field, reason, …), validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Analyze an architecture's capacity",
    description=(
        "Runs the deterministic capacity engine against a revision (default: the current one) under "
        "the given workload, optionally with growth scenarios, and stores the analysis. Synchronous. "
        "Every number says where it comes from (declared, assumed, model estimate); what cannot be "
        "calculated is reported, never guessed. Components and bottlenecks are read with GET "
        "…/components and …/bottlenecks."
    ),
)
async def run_capacity_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: RunCapacityAnalysisRequest,
    current: CurrentUser,
    capacity: CapacityServiceDep,
    limits: RateLimitsDep,
) -> CapacityAnalysisResponse:
    await limits.enforce("run_capacity_analysis", user_id=current.user.id)
    report = await capacity.analyze(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        workload=body.workload.to_domain(),
        revision_number=body.revision,
        models=tuple(body.models) if body.models is not None else None,
        parameters=body.parameters,
        assumptions=tuple(a.to_domain() for a in body.assumptions),
        entries=tuple(body.entries) if body.entries is not None else None,
        label=body.label,
        scenarios=tuple(s.to_domain() for s in body.scenarios),
    )
    return CapacityAnalysisResponse.of(report)


@router.get(
    _ANALYSES,
    response_model=CapacityAnalysisPage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Capacity analyses of an architecture",
    description="Newest first, optionally of one revision.",
)
async def list_capacity_analyses(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    capacity: CapacityServiceDep,
    revision: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CapacityAnalysisPage:
    page = await capacity.list_analyses(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        revision=revision,
        cursor=cursor,
        limit=limit,
    )
    return CapacityAnalysisPage(
        analyses=[CapacityAnalysisSummary.of(r) for r in page.items], next_cursor=page.next_cursor
    )


@router.get(_ONE, response_model=CapacityAnalysisResponse, responses=READ_ONE, summary="A capacity analysis")
async def get_capacity_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    capacity_analysis_id: uuid.UUID,
    current: CurrentUser,
    capacity: CapacityServiceDep,
) -> CapacityAnalysisResponse:
    report = await capacity.get(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=capacity_analysis_id,
        user_id=current.user.id,
    )
    return CapacityAnalysisResponse.of(report)


@router.get(
    _ONE + "/components",
    response_model=ComponentPage,
    responses=READ_ONE,
    summary="Component results of a capacity analysis",
    description="By node id: demand, limits, resource estimates and utilization, each with its source.",
)
async def list_capacity_components(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    capacity_analysis_id: uuid.UUID,
    current: CurrentUser,
    capacity: CapacityServiceDep,
    status_filter: Annotated[ComponentStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ComponentPage:
    page = await capacity.list_components(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=capacity_analysis_id,
        user_id=current.user.id,
        query=ComponentQuery(status_filter, limit=limit),
        cursor=cursor,
    )
    return ComponentPage(
        components=[ComponentModel.of(c.to_dict()) for c in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _ONE + "/bottlenecks",
    response_model=BottleneckList,
    responses=READ_ONE,
    summary="Bottleneck candidates of a capacity analysis",
    description="Modeled limits first, then candidates; worst condition and utilization first.",
)
async def list_capacity_bottlenecks(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    capacity_analysis_id: uuid.UUID,
    current: CurrentUser,
    capacity: CapacityServiceDep,
    certainty: Certainty | None = None,
) -> BottleneckList:
    found = await capacity.list_bottlenecks(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=capacity_analysis_id,
        user_id=current.user.id,
        query=BottleneckQuery(certainty),
    )
    return BottleneckList(bottlenecks=[BottleneckModel.model_validate(b.to_dict()) for b in found])


@router.get(
    _ONE + "/scenarios",
    response_model=ScenarioList,
    responses=READ_ONE,
    summary="Scenario comparisons of a capacity analysis",
    description="Each scenario: summary, bottlenecks, scaling options and comparison with the baseline.",
)
async def list_capacity_scenarios(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    capacity_analysis_id: uuid.UUID,
    current: CurrentUser,
    capacity: CapacityServiceDep,
) -> ScenarioList:
    report = await capacity.get(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=capacity_analysis_id,
        user_id=current.user.id,
    )
    return ScenarioList(scenarios=[scenario_model(s.to_dict()) for s in report.scenarios])


@router.get(
    "/capacity/models",
    response_model=CapacityModelCatalog,
    responses={401: {"model": ErrorResponse}},
    summary="The capacity models",
    description="Every model the engine offers: what it needs and what it estimates.",
)
async def list_capacity_models(current: CurrentUser, capacity: CapacityServiceDep) -> CapacityModelCatalog:
    return CapacityModelCatalog(models=[CapacityModelModel.model_validate(m) for m in capacity.models()])
