import uuid

from fastapi import APIRouter, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import RateLimitsDep, RequirementAnalysisServiceDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.requirement_analysis import (
    AnalysisResponse,
    AnalyzeRequest,
    PromoteRequest,
    PromotionResponse,
)

router = APIRouter(prefix="/projects/{project_id}/requirement-analyses", tags=["requirement analyses"])

ANALYSIS_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, requirement_analysis_not_found"},
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=AnalysisResponse,
    responses=ANALYSIS_ERRORS
    | {
        409: {"model": ErrorResponse, "description": "project_archived"},
        413: {"model": ErrorResponse, "description": "payload_too_large"},
        422: {"model": ErrorResponse, "description": "invalid_requirement_input, validation_error"},
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Analyze requirement text",
    description=(
        "Runs the Requirements Engine over a plain-language description: candidates, ambiguities, "
        "assumptions, conflicts (also against the project's requirements), completeness and "
        "readiness for architecture. The analysis is stored (with the text exactly as written); "
        "**nothing becomes a requirement** until candidates are promoted."
    ),
)
async def analyze_requirements(
    project_id: uuid.UUID,
    body: AnalyzeRequest,
    current: CurrentUser,
    analyses: RequirementAnalysisServiceDep,
    limits: RateLimitsDep,
) -> AnalysisResponse:
    await limits.enforce("analyze_requirements", user_id=current.user.id)
    analysis = await analyses.analyze(project_id=project_id, user_id=current.user.id, raw_input=body.input)
    return AnalysisResponse.from_analysis(analysis)


@router.get(
    "/{analysis_id}",
    response_model=AnalysisResponse,
    responses=ANALYSIS_ERRORS,
    summary="A requirement analysis",
)
async def get_requirement_analysis(
    project_id: uuid.UUID,
    analysis_id: uuid.UUID,
    current: CurrentUser,
    analyses: RequirementAnalysisServiceDep,
) -> AnalysisResponse:
    analysis = await analyses.get(project_id=project_id, analysis_id=analysis_id, user_id=current.user.id)
    return AnalysisResponse.from_analysis(analysis)


@router.post(
    "/{analysis_id}/promote",
    response_model=PromotionResponse,
    responses=ANALYSIS_ERRORS
    | {
        409: {"model": ErrorResponse, "description": "project_archived"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_promotion (details.reason, details.candidateKey), validation_error",
        },
    },
    summary="Promote candidates to requirements",
    description=(
        "Each chosen candidate becomes a **draft** requirement (validated like any other, with its "
        "origin: this analysis and candidate). Idempotent: a candidate promoted before returns the "
        "requirement it became, with created=false."
    ),
)
async def promote_candidates(
    project_id: uuid.UUID,
    analysis_id: uuid.UUID,
    body: PromoteRequest,
    current: CurrentUser,
    analyses: RequirementAnalysisServiceDep,
) -> PromotionResponse:
    promotions = await analyses.promote(
        project_id=project_id,
        analysis_id=analysis_id,
        user_id=current.user.id,
        candidate_keys=body.candidate_keys,
    )
    return PromotionResponse.from_promotions(promotions)
