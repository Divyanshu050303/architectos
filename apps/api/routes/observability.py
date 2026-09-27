"""Observability analyses of an architecture: run, list, read, components, findings, and the analyzer
catalog. Route handlers only translate HTTP to the service and back; the rules live in
core/domain/observability and engines/observability. This is architecture-level analysis of the
modeled system: no telemetry is collected or queried, and nothing here observes ArchitectOS itself."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import ObservabilityServiceDep, RateLimitsDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.observability import (
    ObservabilityAnalysisPage,
    ObservabilityAnalysisResponse,
    ObservabilityAnalysisSummary,
    ObservabilityAnalyzerCatalog,
    ObservabilityAnalyzerModel,
    ObservabilityComponentModel,
    ObservabilityComponentPage,
    ObservabilityFindingModel,
    ObservabilityFindingPage,
    RunObservabilityAnalysisRequest,
)
from core.domain.capacity.results import Certainty
from core.domain.observability.queries import ObservabilityComponentQuery, ObservabilityFindingQuery
from core.domain.observability.results import FindingBasis, FindingCategory, FindingType
from core.domain.observability.values import CoverageState, Dimension
from core.domain.validation.results import Severity

router = APIRouter(tags=["observability"])

_ANALYSES = "/projects/{project_id}/architectures/{architecture_id}/observability-analyses"
_ONE = _ANALYSES + "/{observability_analysis_id}"
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_not_found"},
}
READ_ONE = ERRORS | {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, architecture_not_found, observability_analysis_not_found",
    },
    422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"},
}


@router.post(
    _ANALYSES,
    status_code=status.HTTP_201_CREATED,
    response_model=ObservabilityAnalysisResponse,
    responses=ERRORS
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, architecture_revision_not_found",
        },
        409: {"model": ErrorResponse, "description": "project_archived, architecture_archived"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_observability_request (details: field, reason, …), validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Analyze an architecture's observability",
    description=(
        "Runs the deterministic observability analyzers against a revision (default: the current one), "
        "with the project's policy and in-force requirements, and stores the analysis. Synchronous. "
        "Architecture-level only: it reads what the architecture declares about logs, metrics, traces, "
        "health checks and alerting; it does not collect or query telemetry, does not prove "
        "instrumentation works, and does not compute SLO attainment. What is not declared is reported "
        "as unknown, never as present or absent. Components and findings are read with "
        "GET …/components and …/findings."
    ),
)
async def run_observability_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: RunObservabilityAnalysisRequest,
    current: CurrentUser,
    observability: ObservabilityServiceDep,
    limits: RateLimitsDep,
) -> ObservabilityAnalysisResponse:
    await limits.enforce("run_observability_analysis", user_id=current.user.id)
    report = await observability.analyze(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        revision_number=body.revision,
        scope=tuple(body.scope),
        analyzers=tuple(body.analyzers) if body.analyzers is not None else None,
        requirement_ids=tuple(body.requirement_ids) if body.requirement_ids is not None else None,
        assumptions=tuple(a.to_domain() for a in body.assumptions),
        label=body.label,
    )
    return ObservabilityAnalysisResponse.of(report)


@router.get(
    _ANALYSES,
    response_model=ObservabilityAnalysisPage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Observability analyses of an architecture",
    description="Newest first, optionally of one revision.",
)
async def list_observability_analyses(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    observability: ObservabilityServiceDep,
    revision: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ObservabilityAnalysisPage:
    page = await observability.list_analyses(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        revision=revision,
        cursor=cursor,
        limit=limit,
    )
    return ObservabilityAnalysisPage(
        analyses=[ObservabilityAnalysisSummary.of(r) for r in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _ONE,
    response_model=ObservabilityAnalysisResponse,
    responses=READ_ONE,
    summary="An observability analysis",
    description="Status, coverage summary, requirement and policy checks, limitations.",
)
async def get_observability_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    observability_analysis_id: uuid.UUID,
    current: CurrentUser,
    observability: ObservabilityServiceDep,
) -> ObservabilityAnalysisResponse:
    report = await observability.get(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=observability_analysis_id,
        user_id=current.user.id,
    )
    return ObservabilityAnalysisResponse.of(report)


@router.get(
    _ONE + "/components",
    response_model=ObservabilityComponentPage,
    responses=READ_ONE
    | {
        422: {
            "model": ErrorResponse,
            "description": "invalid_cursor, invalid_observability_request (state without dimension), "
            "validation_error",
        }
    },
    summary="Component coverage of an observability analysis",
    description="By node id: each component's coverage per dimension, its declared facts, what it lacks. "
    "Filter by criticality, or by the coverage state of one dimension (both dimension and state).",
)
async def list_observability_components(  # noqa: PLR0913 -- one argument per filter
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    observability_analysis_id: uuid.UUID,
    current: CurrentUser,
    observability: ObservabilityServiceDep,
    *,
    criticality: Literal["critical", "standard", "not_modeled"] | None = None,
    dimension: Dimension | None = None,
    state: CoverageState | None = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ObservabilityComponentPage:
    page = await observability.list_components(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=observability_analysis_id,
        user_id=current.user.id,
        query=ObservabilityComponentQuery(criticality, dimension, state, limit=limit),
        cursor=cursor,
    )
    return ObservabilityComponentPage(
        components=[ObservabilityComponentModel.model_validate(c.to_dict()) for c in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    _ONE + "/findings",
    response_model=ObservabilityFindingPage,
    responses=READ_ONE,
    summary="Findings of an observability analysis",
    description="In priority order (severity, then basis), for engineering review: control gaps, "
    "potential risks, violations, and what cannot be evaluated.",
)
async def list_observability_findings(  # noqa: PLR0913 -- one argument per filter
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    observability_analysis_id: uuid.UUID,
    current: CurrentUser,
    observability: ObservabilityServiceDep,
    *,
    severity: Severity | None = None,
    finding_type: Annotated[FindingType | None, Query(alias="type")] = None,
    category: FindingCategory | None = None,
    basis: FindingBasis | None = None,
    certainty: Certainty | None = None,
    dimension: Dimension | None = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ObservabilityFindingPage:
    page = await observability.list_findings(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=observability_analysis_id,
        user_id=current.user.id,
        query=ObservabilityFindingQuery(
            severity, finding_type, category, basis, certainty, dimension, limit=limit
        ),
        cursor=cursor,
    )
    return ObservabilityFindingPage(
        findings=[ObservabilityFindingModel.model_validate(f.to_dict()) for f in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/observability/analyzers",
    response_model=ObservabilityAnalyzerCatalog,
    responses={401: {"model": ErrorResponse}},
    summary="The observability analyzers",
    description="In run order: what each reads, its rules, what it cannot evaluate, its limitations.",
)
async def list_observability_analyzers(
    current: CurrentUser, observability: ObservabilityServiceDep
) -> ObservabilityAnalyzerCatalog:
    return ObservabilityAnalyzerCatalog(
        analyzers=[ObservabilityAnalyzerModel.model_validate(a) for a in observability.analyzers()]
    )
