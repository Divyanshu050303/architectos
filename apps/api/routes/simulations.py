"""Simulations of an architecture: run a scenario, list, read, component outcomes, deltas, compare two
simulations, and the catalog of scenario types, evaluators and limits. Route handlers only translate
HTTP to the service and back; the rules live in core/domain/simulations and engines/simulation.
Results are model-based projections: they do not guarantee real-world behavior."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import RateLimitsDep, SimulationServiceDep, get_clock
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.simulation import (
    AnalysisComparisonModel,
    ComponentOutcomeModel,
    ComponentOutcomePage,
    DeltaModel,
    DeltaPage,
    RunSimulationRequest,
    SimulationCatalog,
    SimulationComparisonResponse,
    SimulationPage,
    SimulationResponse,
    SimulationSummary,
)
from core.domain.clock import Clock
from core.domain.simulations.queries import SimulationComponentQuery, SimulationDeltaQuery
from core.domain.simulations.values import AnalysisKind, Impact

router = APIRouter(tags=["simulations"])

_SIMULATIONS = "/projects/{project_id}/architectures/{architecture_id}/simulations"
_ONE = _SIMULATIONS + "/{simulation_id}"
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_not_found"},
}
READ_ONE = ERRORS | {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, architecture_not_found, simulation_not_found",
    },
    422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"},
}


@router.post(
    _SIMULATIONS,
    status_code=status.HTTP_201_CREATED,
    response_model=SimulationResponse,
    responses=ERRORS
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, architecture_revision_not_found, "
            "pricing_snapshot_not_found",
        },
        409: {"model": ErrorResponse, "description": "project_archived, architecture_archived"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_simulation_request (details: field, reason, element_id or limit), "
            "invalid_workload_profile, invalid_capacity_quantity, validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Simulate a scenario against an architecture",
    description=(
        "Applies the scenario to an in-memory copy of a revision (default: the current one) and evaluates "
        "it, next to the unchanged baseline, with the Capacity, Reliability and Cost Engines as supported. "
        "Synchronous and bounded by the execution limits (GET /simulation/catalog). The architecture is "
        "never changed. Results are model-based projections: they do not guarantee real-world "
        "performance, availability, cost or failure behavior; missing inputs stay unknown or unsupported."
    ),
)
async def run_simulation(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: RunSimulationRequest,
    current: CurrentUser,
    simulations: SimulationServiceDep,
    limits: RateLimitsDep,
    clock: Annotated[Clock, Depends(get_clock)],
) -> SimulationResponse:
    await limits.enforce("run_simulation", user_id=current.user.id)
    report = await simulations.simulate(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        scenario=body.scenario.to_domain(),
        revision_number=body.revision,
        analyses=tuple(body.analyses) if body.analyses is not None else None,
        workload=body.workload.to_domain() if body.workload is not None else None,
        entries=tuple(body.entries) if body.entries is not None else None,
        pricing=body.pricing.to_domain(clock().date()) if body.pricing is not None else None,
        assumptions=tuple(a.to_domain() for a in body.assumptions),
        label=body.label,
    )
    return SimulationResponse.of(report)


@router.get(
    _SIMULATIONS,
    response_model=SimulationPage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Simulations of an architecture",
    description="Newest first, optionally of one revision.",
)
async def list_simulations(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    simulations: SimulationServiceDep,
    revision: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> SimulationPage:
    page = await simulations.list_simulations(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        revision=revision,
        cursor=cursor,
        limit=limit,
    )
    return SimulationPage(
        simulations=[SimulationSummary.of(r) for r in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _ONE,
    response_model=SimulationResponse,
    responses=READ_ONE,
    summary="A simulation",
    description="Status, summary, runs, entry impacts, the overlay evaluated, assumptions, trace and "
    "limitations.",
)
async def get_simulation(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    simulation_id: uuid.UUID,
    current: CurrentUser,
    simulations: SimulationServiceDep,
) -> SimulationResponse:
    report = await simulations.get(
        project_id=project_id,
        architecture_id=architecture_id,
        simulation_id=simulation_id,
        user_id=current.user.id,
    )
    return SimulationResponse.of(report)


@router.get(
    _ONE + "/components",
    response_model=ComponentOutcomePage,
    responses=READ_ONE,
    summary="Component outcomes of a simulation",
    description="By node id: whether each component is unavailable, what the scenario changed on it, its "
    "failure impact.",
)
async def list_simulation_components(  # noqa: PLR0913 -- one argument per filter
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    simulation_id: uuid.UUID,
    current: CurrentUser,
    simulations: SimulationServiceDep,
    *,
    unavailable: bool | None = None,
    impact: Impact | None = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ComponentOutcomePage:
    page = await simulations.list_components(
        project_id=project_id,
        architecture_id=architecture_id,
        simulation_id=simulation_id,
        user_id=current.user.id,
        query=SimulationComponentQuery(unavailable, impact, limit=limit),
        cursor=cursor,
    )
    return ComponentOutcomePage(
        components=[ComponentOutcomeModel.model_validate(c.to_dict()) for c in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    _ONE + "/deltas",
    response_model=DeltaPage,
    responses=READ_ONE,
    summary="Baseline and scenario compared",
    description="In canonical order (analysis, element, metric): baseline and scenario values with their "
    "unit, the difference and percentage where defined, and why a value is not comparable.",
)
async def list_simulation_deltas(  # noqa: PLR0913 -- one argument per filter
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    simulation_id: uuid.UUID,
    current: CurrentUser,
    simulations: SimulationServiceDep,
    *,
    analysis: AnalysisKind | None = None,
    element_id: Annotated[str | None, Query(alias="elementId", max_length=128)] = None,
    comparable: bool | None = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DeltaPage:
    page = await simulations.list_deltas(
        project_id=project_id,
        architecture_id=architecture_id,
        simulation_id=simulation_id,
        user_id=current.user.id,
        query=SimulationDeltaQuery(analysis, element_id, comparable, limit=limit),
        cursor=cursor,
    )
    return DeltaPage(
        deltas=[DeltaModel.model_validate(d.to_dict()) for d in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _ONE + "/comparisons/{other_simulation_id}",
    response_model=SimulationComparisonResponse,
    responses=READ_ONE
    | {
        422: {
            "model": ErrorResponse,
            "description": "invalid_simulation_request (a simulation without a result), validation_error",
        }
    },
    summary="Compare two simulations",
    description="The other simulation's scenario against this one's, per analysis, only where both ran on "
    "one common baseline with the same models and engine; otherwise marked not comparable with the reason.",
)
async def compare_simulations(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    simulation_id: uuid.UUID,
    other_simulation_id: uuid.UUID,
    current: CurrentUser,
    simulations: SimulationServiceDep,
) -> SimulationComparisonResponse:
    comparison = await simulations.compare(
        project_id=project_id,
        architecture_id=architecture_id,
        first_id=simulation_id,
        second_id=other_simulation_id,
        user_id=current.user.id,
    )
    return SimulationComparisonResponse(
        first=comparison.first_fingerprint,
        second=comparison.second_fingerprint,
        comparable=comparison.comparable,
        analyses=[AnalysisComparisonModel.model_validate(a.to_dict()) for a in comparison.analyses],
        deltas=[DeltaModel.model_validate(d.to_dict()) for d in comparison.deltas],
    )


@router.get(
    "/simulation/catalog",
    response_model=SimulationCatalog,
    responses={401: {"model": ErrorResponse}},
    summary="Supported scenario types, evaluators and execution limits",
    description="Each scenario type's inputs and units, elements, overlay, analyses, unsupported conditions "
    "and limit; each evaluator; and the limits in force.",
)
async def get_simulation_catalog(
    current: CurrentUser, simulations: SimulationServiceDep
) -> SimulationCatalog:
    return SimulationCatalog.model_validate(simulations.catalog())
