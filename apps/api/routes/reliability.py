"""Reliability analyses of an architecture: run, list, read, components, findings, and the model
catalog. Route handlers only translate HTTP to the service and back; the rules live in
core/domain/reliability and engines/reliability."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import RateLimitsDep, ReliabilityServiceDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.reliability import (
    FindingModel,
    FindingPage,
    ReliabilityAnalysisPage,
    ReliabilityAnalysisResponse,
    ReliabilityAnalysisSummary,
    ReliabilityComponentModel,
    ReliabilityComponentPage,
    ReliabilityModelCatalog,
    ReliabilityModelModel,
    RunReliabilityAnalysisRequest,
)
from core.domain.capacity.results import Certainty, ComponentStatus
from core.domain.reliability.queries import ReliabilityComponentQuery, ReliabilityFindingQuery
from core.domain.reliability.results import FindingType
from core.domain.validation.results import Severity

router = APIRouter(tags=["reliability"])

_ANALYSES = "/projects/{project_id}/architectures/{architecture_id}/reliability-analyses"
_ONE = _ANALYSES + "/{reliability_analysis_id}"
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_not_found"},
}
READ_ONE = ERRORS | {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, architecture_not_found, reliability_analysis_not_found",
    },
    422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"},
}


@router.post(
    _ANALYSES,
    status_code=status.HTTP_201_CREATED,
    response_model=ReliabilityAnalysisResponse,
    responses=ERRORS
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, architecture_revision_not_found",
        },
        409: {"model": ErrorResponse, "description": "project_archived, architecture_archived"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_reliability_request, invalid_capacity_quantity "
            "(details: field, reason, …), validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Analyze an architecture's reliability",
    description=(
        "Runs the deterministic reliability engine against a revision (default: the current one) and "
        "stores the analysis. Synchronous. Availability, recovery and data loss come only from what "
        "the architecture declares and stated models; what cannot be established is unknown, never "
        "0 or 1. Estimates, not guaranteed uptime. Components and findings are read with GET "
        "…/components and …/findings."
    ),
)
async def run_reliability_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: RunReliabilityAnalysisRequest,
    current: CurrentUser,
    reliability: ReliabilityServiceDep,
    limits: RateLimitsDep,
) -> ReliabilityAnalysisResponse:
    await limits.enforce("run_reliability_analysis", user_id=current.user.id)
    report = await reliability.analyze(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        revision_number=body.revision,
        entries=tuple(body.entries) if body.entries is not None else None,
        objectives=tuple(o.to_domain() for o in body.objectives),
        assumptions=tuple(a.to_domain() for a in body.assumptions),
        label=body.label,
    )
    return ReliabilityAnalysisResponse.of(report)


@router.get(
    _ANALYSES,
    response_model=ReliabilityAnalysisPage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Reliability analyses of an architecture",
    description="Newest first, optionally of one revision.",
)
async def list_reliability_analyses(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    reliability: ReliabilityServiceDep,
    revision: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ReliabilityAnalysisPage:
    page = await reliability.list_analyses(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        revision=revision,
        cursor=cursor,
        limit=limit,
    )
    return ReliabilityAnalysisPage(
        analyses=[ReliabilityAnalysisSummary.of(r) for r in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _ONE,
    response_model=ReliabilityAnalysisResponse,
    responses=READ_ONE,
    summary="A reliability analysis",
    description="Status, summary, request paths with their availability and scope, objective verdicts.",
)
async def get_reliability_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    reliability_analysis_id: uuid.UUID,
    current: CurrentUser,
    reliability: ReliabilityServiceDep,
) -> ReliabilityAnalysisResponse:
    report = await reliability.get(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=reliability_analysis_id,
        user_id=current.user.id,
    )
    return ReliabilityAnalysisResponse.of(report)


@router.get(
    _ONE + "/components",
    response_model=ReliabilityComponentPage,
    responses=READ_ONE,
    summary="Component results of a reliability analysis",
    description="By node id: availability, recovery time and data loss estimates, with their inputs.",
)
async def list_reliability_components(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    reliability_analysis_id: uuid.UUID,
    current: CurrentUser,
    reliability: ReliabilityServiceDep,
    *,
    status_filter: Annotated[ComponentStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ReliabilityComponentPage:
    page = await reliability.list_components(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=reliability_analysis_id,
        user_id=current.user.id,
        query=ReliabilityComponentQuery(status_filter, limit=limit),
        cursor=cursor,
    )
    return ReliabilityComponentPage(
        components=[ReliabilityComponentModel.of(c) for c in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _ONE + "/findings",
    response_model=FindingPage,
    responses=READ_ONE,
    summary="Findings of a reliability analysis",
    description="Most severe first: single points of failure, redundancy, failover, objectives, for review.",
)
async def list_reliability_findings(  # noqa: PLR0913 -- one argument per filter
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    reliability_analysis_id: uuid.UUID,
    current: CurrentUser,
    reliability: ReliabilityServiceDep,
    *,
    severity: Severity | None = None,
    finding_type: Annotated[FindingType | None, Query(alias="type")] = None,
    certainty: Certainty | None = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> FindingPage:
    page = await reliability.list_findings(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=reliability_analysis_id,
        user_id=current.user.id,
        query=ReliabilityFindingQuery(severity, finding_type, certainty, limit=limit),
        cursor=cursor,
    )
    return FindingPage(
        findings=[FindingModel.model_validate(f.to_dict()) for f in page.items], next_cursor=page.next_cursor
    )


@router.get(
    "/reliability/models",
    response_model=ReliabilityModelCatalog,
    responses={401: {"model": ErrorResponse}},
    summary="The reliability models",
    description="Component models in precedence order, then the architecture steps: formulas and limits.",
)
async def list_reliability_models(
    current: CurrentUser, reliability: ReliabilityServiceDep
) -> ReliabilityModelCatalog:
    return ReliabilityModelCatalog(
        models=[ReliabilityModelModel.model_validate(m) for m in reliability.models()]
    )
