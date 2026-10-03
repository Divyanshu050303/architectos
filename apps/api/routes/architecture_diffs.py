"""Architecture diffs of a project: compare two exact states (revisions, or an agent run's candidate),
list and read stored diffs, and ask for an AI explanation of one (appended; the diff never changes).
Nothing here changes an architecture, a revision, an agent run or an analysis."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import DiffServiceDep, RateLimitsDep
from apps.api.schemas.architecture_diffs import (
    DiffCreateRequest,
    DiffPage,
    DiffResponse,
    DiffSummary,
    ExplanationRunResponse,
)
from apps.api.schemas.common import ErrorResponse

router = APIRouter(tags=["architecture diffs"])

_DIFFS = "/projects/{project_id}/architecture-diffs"
_DIFF = _DIFFS + "/{diff_id}"

READ: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_diff_not_found"},
}
EXPLAINS = READ | {
    409: {"model": ErrorResponse, "description": "project_archived"},
    429: {"model": ErrorResponse, "description": "rate_limited"},
}
COMPARES = EXPLAINS | {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, compared_state_not_found (details: side — missing, hidden or "
        "not comparable, one answer), capacity_analysis_not_found, cost_analysis_not_found, "
        "pricing_snapshot_not_found",
    },
    422: {
        "model": ErrorResponse,
        "description": "invalid_diff_request (details: field, reason), architecture_diff_too_large "
        "(details: limit, value), validation_error",
    },
}


@router.post(
    _DIFFS,
    status_code=status.HTTP_201_CREATED,
    response_model=DiffResponse,
    responses=COMPARES,
    summary="Compare two architecture states",
    description=(
        "Compares two exact states — each a revision or an architecture agent run's candidate — "
        "deterministically: every change (matched by stable id, a secret's values never shown) in exactly "
        "one rule-formed group; the requirements the changes touch (through traces and the validation "
        "engine's verdicts) and the ADRs that may require review; validation, reliability, security and "
        "observability on both states (findings introduced and resolved by stable id), capacity and cost "
        "only on a named stored analysis's inputs. Stored, immutable. No score, no winner. With "
        "explain=true, one AI explanation run is appended."
    ),
)
async def create_architecture_diff(
    project_id: uuid.UUID,
    body: DiffCreateRequest,
    current: CurrentUser,
    diffs: DiffServiceDep,
    limits: RateLimitsDep,
) -> DiffResponse:
    request = body.to_domain()
    await limits.enforce("compare_architectures", user_id=current.user.id)
    if request.explain:
        await limits.enforce("explain_architecture_diff", user_id=current.user.id)
    report = await diffs.compare(project_id=project_id, user_id=current.user.id, request=request)
    return DiffResponse.of(report)


@router.get(
    _DIFFS,
    response_model=DiffPage,
    responses=READ | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Architecture diffs of a project",
    description="Newest first, without their parts; optionally those with a revision of one architecture.",
)
async def list_architecture_diffs(
    project_id: uuid.UUID,
    current: CurrentUser,
    diffs: DiffServiceDep,
    *,
    architecture_id: Annotated[uuid.UUID | None, Query(alias="architectureId")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DiffPage:
    page = await diffs.list(
        project_id=project_id,
        user_id=current.user.id,
        architecture_id=architecture_id,
        cursor=cursor,
        limit=limit,
    )
    return DiffPage(diffs=[DiffSummary.of(d) for d in page.items], next_cursor=page.next_cursor)


@router.get(
    _DIFF,
    response_model=DiffResponse,
    responses=READ,
    summary="An architecture diff",
    description="Its changes, groups, impacts and engines' comparison, with every explanation run (oldest "
    "first). Never the prompt, the retrieved text or the model's raw output.",
)
async def get_architecture_diff(
    project_id: uuid.UUID, diff_id: uuid.UUID, current: CurrentUser, diffs: DiffServiceDep
) -> DiffResponse:
    report = await diffs.get(project_id=project_id, diff_id=diff_id, user_id=current.user.id)
    return DiffResponse.of(report)


@router.post(
    _DIFF + "/explanations",
    status_code=status.HTTP_201_CREATED,
    response_model=ExplanationRunResponse,
    responses=EXPLAINS,
    summary="Explain an architecture diff",
    description=(
        "Appends an explanation run: project knowledge through the retriever, then one structured model "
        "call (at most one retry) over the stored diff. Every statement cites the changes, groups, findings, "
        "requirements, ADRs or passages it rests on, or is labelled an inference; anything else is refused "
        "whole. Identical states need no explanation (not_needed: no model call). Without a configured model "
        "the run fails llm_unavailable. The diff itself never changes."
    ),
)
async def explain_architecture_diff(
    project_id: uuid.UUID,
    diff_id: uuid.UUID,
    current: CurrentUser,
    diffs: DiffServiceDep,
    limits: RateLimitsDep,
) -> ExplanationRunResponse:
    await limits.enforce("explain_architecture_diff", user_id=current.user.id)
    run = await diffs.explain(project_id=project_id, diff_id=diff_id, user_id=current.user.id)
    return ExplanationRunResponse.of(run)
