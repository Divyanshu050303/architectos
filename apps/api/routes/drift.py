"""Drift detection of a project: compare an architecture revision with a stored discovery run, read the
analyses and their findings, follow drift items across analyses and review them, and confirm the
identities matching relies on. Nothing here changes an architecture: drift is reported, never
remediated."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import DriftServiceDep, RateLimitsDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.drift import (
    DriftAnalysisPage,
    DriftAnalysisRequest,
    DriftAnalysisResponse,
    DriftAnalysisSummary,
    DriftFindingModel,
    DriftFindingsResponse,
    DriftItemPage,
    DriftItemResponse,
    IdentityMappingRequest,
    IdentityMappingResponse,
    IdentityMappingsResponse,
    ReviewRequest,
)
from core.domain.drift.values import AnalysisStatus, Classification, FindingType, ReviewStatus

router = APIRouter(tags=["drift"])

_ANALYSES = "/projects/{project_id}/drift-analyses"
_ANALYSIS = _ANALYSES + "/{drift_analysis_id}"
_ITEMS = "/projects/{project_id}/drift-items"
_ITEM = _ITEMS + "/{item_id}"
_MAPPINGS = "/projects/{project_id}/architectures/{architecture_id}/identity-mappings"

READ: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, drift_analysis_not_found"},
}
CURSOR: dict[int | str, dict[str, object]] = {
    422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}
}


@router.post(
    _ANALYSES,
    status_code=status.HTTP_201_CREATED,
    response_model=DriftAnalysisResponse,
    responses=READ
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, architecture_revision_not_found, "
            "discovery_run_not_found",
        },
        409: {"model": ErrorResponse, "description": "project_archived"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_drift_request (details: field, reason), validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Run a drift analysis",
    description=(
        "Compares the exact baseline revision with the stored discovery run's result — what the sources "
        "declare, not the running system — and stores the compatibility, coverage and classified findings "
        "(confirmed, potential, not_comparable, unknown; no score). A removal is confirmed only where the "
        "baseline element's source was read completely; inputs that cannot be compared give "
        "incompatible_inputs and no findings. Findings are folded into the architecture's drift items. "
        "Synchronous. Neither the architecture nor the discovery run is changed."
    ),
)
async def run_drift_analysis(
    project_id: uuid.UUID,
    body: DriftAnalysisRequest,
    current: CurrentUser,
    drift: DriftServiceDep,
    limits: RateLimitsDep,
) -> DriftAnalysisResponse:
    await limits.enforce("run_drift_analysis", user_id=current.user.id)
    analysis = await drift.analyze(project_id=project_id, user_id=current.user.id, request=body.to_domain())
    return DriftAnalysisResponse.of_analysis(analysis)


@router.get(
    _ANALYSES,
    response_model=DriftAnalysisPage,
    responses=READ | CURSOR,
    summary="Drift analyses of a project",
    description="Newest first, with their summaries; optionally of one architecture (its drift history) "
    "or one status.",
)
async def list_drift_analyses(
    project_id: uuid.UUID,
    current: CurrentUser,
    drift: DriftServiceDep,
    *,
    architecture_id: Annotated[uuid.UUID | None, Query(alias="architectureId")] = None,
    analysis_status: Annotated[AnalysisStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DriftAnalysisPage:
    page = await drift.list_analyses(
        project_id=project_id,
        user_id=current.user.id,
        architecture_id=architecture_id,
        status=analysis_status,
        cursor=cursor,
        limit=limit,
    )
    analyses = [DriftAnalysisSummary.of(a) for a in page.items]
    return DriftAnalysisPage(analyses=analyses, next_cursor=page.next_cursor)


@router.get(
    _ANALYSIS,
    response_model=DriftAnalysisResponse,
    responses=READ,
    summary="A drift analysis",
    description="What was compared (baseline revision and content hash, discovery run fingerprints), the "
    "compatibility per dimension, the coverage, warnings and rule versions. Findings: GET .../findings.",
)
async def get_drift_analysis(
    project_id: uuid.UUID, drift_analysis_id: uuid.UUID, current: CurrentUser, drift: DriftServiceDep
) -> DriftAnalysisResponse:
    analysis = await drift.get(project_id=project_id, analysis_id=drift_analysis_id, user_id=current.user.id)
    return DriftAnalysisResponse.of_analysis(analysis)


@router.get(
    _ANALYSIS + "/findings",
    response_model=DriftFindingsResponse,
    responses=READ,
    summary="A drift analysis's findings",
    description="Each difference with its classification, explanation, evidence and source locations, the "
    "baseline and discovered values (secrets redacted) and context from stored analyses.",
)
async def list_drift_findings(
    project_id: uuid.UUID,
    drift_analysis_id: uuid.UUID,
    current: CurrentUser,
    drift: DriftServiceDep,
    classification: Classification | None = None,
    finding_type: Annotated[FindingType | None, Query(alias="type")] = None,
) -> DriftFindingsResponse:
    findings = await drift.findings(
        project_id=project_id,
        analysis_id=drift_analysis_id,
        user_id=current.user.id,
        classification=classification,
        finding_type=finding_type,
    )
    return DriftFindingsResponse.of(drift_analysis_id, findings)


@router.get(
    _ANALYSIS + "/findings/{finding_id}",
    response_model=DriftFindingModel,
    responses=READ,
    summary="A drift finding",
    description="Its id is stable: the same difference has the same id in every analysis.",
)
async def get_drift_finding(
    project_id: uuid.UUID,
    drift_analysis_id: uuid.UUID,
    finding_id: Annotated[str, Path(max_length=64)],
    current: CurrentUser,
    drift: DriftServiceDep,
) -> DriftFindingModel:
    found = await drift.finding(
        project_id=project_id, analysis_id=drift_analysis_id, finding_id=finding_id, user_id=current.user.id
    )
    return DriftFindingModel.model_validate(found.to_dict())


@router.get(
    _ITEMS,
    response_model=DriftItemPage,
    responses=READ | CURSOR,
    summary="Drift items of a project",
    description="Differences followed across analyses, with their review status and history; optionally "
    "of one architecture or one review status.",
)
async def list_drift_items(
    project_id: uuid.UUID,
    current: CurrentUser,
    drift: DriftServiceDep,
    *,
    architecture_id: Annotated[uuid.UUID | None, Query(alias="architectureId")] = None,
    review_status: Annotated[ReviewStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DriftItemPage:
    page = await drift.list_items(
        project_id=project_id,
        user_id=current.user.id,
        architecture_id=architecture_id,
        status=review_status,
        cursor=cursor,
        limit=limit,
    )
    return DriftItemPage(items=[DriftItemResponse.of(i) for i in page.items], next_cursor=page.next_cursor)


ITEM_READ = READ | {404: {"model": ErrorResponse, "description": "project_not_found, drift_item_not_found"}}


@router.get(
    _ITEM,
    response_model=DriftItemResponse,
    responses=ITEM_READ,
    summary="A drift item",
    description="Its latest detection, review status, full review history, links and source artifacts.",
)
async def get_drift_item(
    project_id: uuid.UUID, item_id: uuid.UUID, current: CurrentUser, drift: DriftServiceDep
) -> DriftItemResponse:
    item = await drift.get_item(project_id=project_id, item_id=item_id, user_id=current.user.id)
    return DriftItemResponse.of(item)


@router.post(
    _ITEM + "/review",
    response_model=DriftItemResponse,
    responses=ITEM_READ
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, drift_item_not_found, drift_analysis_not_found",
        },
        409: {
            "model": ErrorResponse,
            "description": "invalid_drift_review_action (details: action, status, reason), project_archived",
        },
        422: {"model": ErrorResponse, "description": "validation_error"},
    },
    summary="Review a drift item",
    description="Acknowledge, investigate, accept, dismiss (with a reason), resolve (with a later analysis "
    "that inspected its sources and no longer detects it, or a revision link), reopen, note or link a "
    "decision, migration plan, evolution analysis or revision of this project. Recorded and audited; the "
    "architecture is never changed.",
)
async def review_drift_item(
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ReviewRequest,
    current: CurrentUser,
    drift: DriftServiceDep,
) -> DriftItemResponse:
    item = await drift.review(
        project_id=project_id,
        item_id=item_id,
        user_id=current.user.id,
        action=body.action,
        note=body.note,
        link=body.link.to_domain() if body.link else None,
        evidence_analysis_id=body.evidence_analysis_id,
    )
    return DriftItemResponse.of(item)


MAPPING_READ = READ | {
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_not_found"}
}


@router.get(
    _MAPPINGS,
    response_model=IdentityMappingsResponse,
    responses=MAPPING_READ,
    summary="Confirmed identity mappings",
    description="Which discovered entity a node of the architecture is, as people confirmed it.",
)
async def list_identity_mappings(
    project_id: uuid.UUID, architecture_id: uuid.UUID, current: CurrentUser, drift: DriftServiceDep
) -> IdentityMappingsResponse:
    mappings = await drift.mappings(
        project_id=project_id, architecture_id=architecture_id, user_id=current.user.id
    )
    return IdentityMappingsResponse(
        architecture_id=architecture_id, mappings=[IdentityMappingResponse.of(m) for m in mappings]
    )


@router.post(
    _MAPPINGS,
    status_code=status.HTTP_201_CREATED,
    response_model=IdentityMappingResponse,
    responses=MAPPING_READ
    | {
        409: {"model": ErrorResponse, "description": "project_archived"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_drift_request (details: field, reason), validation_error",
        },
    },
    summary="Confirm an identity",
    description="States that a node of the architecture's current revision and a discovered entity are "
    "the same (null discoveredKey retracts it). Used by later analyses for matching only — never by name; "
    "the architecture is not changed.",
)
async def confirm_identity_mapping(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: IdentityMappingRequest,
    current: CurrentUser,
    drift: DriftServiceDep,
) -> IdentityMappingResponse:
    mapping = await drift.confirm_identity(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        baseline_id=body.baseline_id,
        discovered_key=body.discovered_key,
        note=body.note,
    )
    return IdentityMappingResponse.of(mapping)
