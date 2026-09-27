"""Security analyses of an architecture: run, list, read, components, findings, and the analyzer
catalog. Route handlers only translate HTTP to the service and back; the rules live in
core/domain/security and engines/security. This is architecture-level analysis of the modeled
system, not ArchitectOS's own authentication or authorization."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import RateLimitsDep, SecurityServiceDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.security import (
    RunSecurityAnalysisRequest,
    SecurityAnalysisPage,
    SecurityAnalysisResponse,
    SecurityAnalysisSummary,
    SecurityAnalyzerCatalog,
    SecurityAnalyzerModel,
    SecurityComponentModel,
    SecurityComponentPage,
    SecurityFindingModel,
    SecurityFindingPage,
)
from core.domain.capacity.results import Certainty
from core.domain.security.queries import SecurityComponentQuery, SecurityFindingQuery
from core.domain.security.results import Coverage, FindingBasis, FindingCategory, FindingType, StrideCategory
from core.domain.validation.results import Severity

router = APIRouter(tags=["security"])

_ANALYSES = "/projects/{project_id}/architectures/{architecture_id}/security-analyses"
_ONE = _ANALYSES + "/{security_analysis_id}"
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_not_found"},
}
READ_ONE = ERRORS | {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, architecture_not_found, security_analysis_not_found",
    },
    422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"},
}


@router.post(
    _ANALYSES,
    status_code=status.HTTP_201_CREATED,
    response_model=SecurityAnalysisResponse,
    responses=ERRORS
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, architecture_revision_not_found",
        },
        409: {"model": ErrorResponse, "description": "project_archived, architecture_archived"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_security_request (details: field, reason, …), validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Analyze an architecture's security",
    description=(
        "Runs the deterministic security analyzers against a revision (default: the current one), with "
        "the project's policy and in-force security requirements, and stores the analysis. Synchronous. "
        "Architecture-level only: findings come from what the architecture declares; a control that is "
        "not modeled is reported as such, never taken as present or absent. It does not prove the "
        "absence of vulnerabilities. Components and findings are read with GET …/components and "
        "…/findings."
    ),
)
async def run_security_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: RunSecurityAnalysisRequest,
    current: CurrentUser,
    security: SecurityServiceDep,
    limits: RateLimitsDep,
) -> SecurityAnalysisResponse:
    await limits.enforce("run_security_analysis", user_id=current.user.id)
    report = await security.analyze(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        revision_number=body.revision,
        scope=tuple(body.scope),
        analyzers=tuple(body.analyzers) if body.analyzers is not None else None,
        assumptions=tuple(a.to_domain() for a in body.assumptions),
        label=body.label,
    )
    return SecurityAnalysisResponse.of(report)


@router.get(
    _ANALYSES,
    response_model=SecurityAnalysisPage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Security analyses of an architecture",
    description="Newest first, optionally of one revision.",
)
async def list_security_analyses(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    security: SecurityServiceDep,
    revision: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> SecurityAnalysisPage:
    page = await security.list_analyses(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        revision=revision,
        cursor=cursor,
        limit=limit,
    )
    return SecurityAnalysisPage(
        analyses=[SecurityAnalysisSummary.of(r) for r in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _ONE,
    response_model=SecurityAnalysisResponse,
    responses=READ_ONE,
    summary="A security analysis",
    description="Status, summary, trust zones, requirement and policy checks, limitations.",
)
async def get_security_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    security_analysis_id: uuid.UUID,
    current: CurrentUser,
    security: SecurityServiceDep,
) -> SecurityAnalysisResponse:
    report = await security.get(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=security_analysis_id,
        user_id=current.user.id,
    )
    return SecurityAnalysisResponse.of(report)


@router.get(
    _ONE + "/components",
    response_model=SecurityComponentPage,
    responses=READ_ONE,
    summary="Component results of a security analysis",
    description="By node id: the security facts each component declares, what it lacks, its trust zones.",
)
async def list_security_components(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    security_analysis_id: uuid.UUID,
    current: CurrentUser,
    security: SecurityServiceDep,
    *,
    coverage: Coverage | None = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SecurityComponentPage:
    page = await security.list_components(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=security_analysis_id,
        user_id=current.user.id,
        query=SecurityComponentQuery(coverage, limit=limit),
        cursor=cursor,
    )
    return SecurityComponentPage(
        components=[SecurityComponentModel.model_validate(c.to_dict()) for c in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    _ONE + "/findings",
    response_model=SecurityFindingPage,
    responses=READ_ONE,
    summary="Findings of a security analysis",
    description="Most severe first, for engineering review: control gaps, potential risks, violations, "
    "what cannot be evaluated, and STRIDE threat candidates.",
)
async def list_security_findings(  # noqa: PLR0913 -- one argument per filter
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    security_analysis_id: uuid.UUID,
    current: CurrentUser,
    security: SecurityServiceDep,
    *,
    severity: Severity | None = None,
    finding_type: Annotated[FindingType | None, Query(alias="type")] = None,
    category: FindingCategory | None = None,
    basis: FindingBasis | None = None,
    certainty: Certainty | None = None,
    threat: StrideCategory | None = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SecurityFindingPage:
    page = await security.list_findings(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=security_analysis_id,
        user_id=current.user.id,
        query=SecurityFindingQuery(severity, finding_type, category, basis, certainty, threat, limit=limit),
        cursor=cursor,
    )
    return SecurityFindingPage(
        findings=[SecurityFindingModel.model_validate(f.to_dict()) for f in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/security/analyzers",
    response_model=SecurityAnalyzerCatalog,
    responses={401: {"model": ErrorResponse}},
    summary="The security analyzers",
    description="In run order: what each reads, its rules, what it cannot evaluate, its limitations.",
)
async def list_security_analyzers(
    current: CurrentUser, security: SecurityServiceDep
) -> SecurityAnalyzerCatalog:
    return SecurityAnalyzerCatalog(
        analyzers=[SecurityAnalyzerModel.model_validate(a) for a in security.analyzers()]
    )
