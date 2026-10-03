"""Architecture workflows of a project: queue a goal, list and read workflows (with their candidates and
steps), give the input one waits for, cancel one, and — a person's decision — reject the review package
or approve one candidate through the architecture workflow. A worker carries workflows forward; nothing
here runs a model or an engine, and nothing changes an architecture except approving, explicitly."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import RateLimitsDep, WorkflowServiceDep
from apps.api.schemas.architecture_workflows import (
    WorkflowApprovedResponse,
    WorkflowApproveRequest,
    WorkflowCandidateResponse,
    WorkflowInputRequest,
    WorkflowPage,
    WorkflowRejectRequest,
    WorkflowRequest,
    WorkflowResponse,
    WorkflowSummary,
)
from apps.api.schemas.common import ErrorResponse
from core.domain.architecture_workflow.values import WorkflowStatus
from core.domain.architecture_workflow.workflow_service import WorkflowDetail

router = APIRouter(tags=["architecture workflows"])

_WORKFLOWS = "/projects/{project_id}/architecture-workflows"
_WORKFLOW = _WORKFLOWS + "/{workflow_id}"

READ: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_workflow_not_found"},
}
WRITE = READ | {
    409: {"model": ErrorResponse, "description": "invalid_workflow_transition, project_archived"},
    422: {
        "model": ErrorResponse,
        "description": "invalid_workflow_request (details: field, reason), validation_error",
    },
}


@router.post(
    _WORKFLOWS,
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WorkflowResponse,
    responses=WRITE
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, requirement_set_not_found, architecture_not_found, "
            "architecture_revision_not_found, capacity_analysis_not_found, cost_analysis_not_found",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Start an architecture workflow",
    description=(
        "Queues a goal. A worker then carries it, step by step and within its budget, through "
        "requirements (without a requirement set the goal is analyzed and the workflow waits for you to "
        "confirm one), project knowledge, generation by the architecture agent, validation, the analyses "
        "its inputs allow, improvement rounds (deterministic evolution rules first, the agent second) and "
        "comparison, to a review package. Every action comes from a closed server-side registry and runs "
        "with your current permissions. No architecture is changed: you approve or reject."
    ),
)
async def start_architecture_workflow(
    project_id: uuid.UUID,
    body: WorkflowRequest,
    current: CurrentUser,
    workflows: WorkflowServiceDep,
    limits: RateLimitsDep,
) -> WorkflowResponse:
    goal, budget = body.to_domain()
    await limits.enforce("run_architecture_workflow", user_id=current.user.id)
    workflow = await workflows.start(project_id=project_id, user_id=current.user.id, goal=goal, limits=budget)
    return WorkflowResponse.of(WorkflowDetail(workflow, (), ()))


@router.get(
    _WORKFLOWS,
    response_model=WorkflowPage,
    responses=READ | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Architecture workflows of a project",
    description="Newest first, without their parts; optionally of one status.",
)
async def list_architecture_workflows(
    project_id: uuid.UUID,
    current: CurrentUser,
    workflows: WorkflowServiceDep,
    *,
    workflow_status: Annotated[WorkflowStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> WorkflowPage:
    page = await workflows.list(
        project_id=project_id, user_id=current.user.id, status=workflow_status, cursor=cursor, limit=limit
    )
    return WorkflowPage(workflows=[WorkflowSummary.of(w) for w in page.items], next_cursor=page.next_cursor)


@router.get(
    _WORKFLOW,
    response_model=WorkflowResponse,
    responses=READ,
    summary="An architecture workflow",
    description="Its status and stage, what it waits for, its candidates (lineage, validation, "
    "approvability), every step it took (inputs and outputs as references), its usage against its "
    "budget, its limitations and the decision. Never a prompt, retrieved text or a model's raw output.",
)
async def get_architecture_workflow(
    project_id: uuid.UUID, workflow_id: uuid.UUID, current: CurrentUser, workflows: WorkflowServiceDep
) -> WorkflowResponse:
    detail = await workflows.get(project_id=project_id, workflow_id=workflow_id, user_id=current.user.id)
    return WorkflowResponse.of(detail)


@router.get(
    _WORKFLOW + "/candidates/{workflow_candidate_id}",
    response_model=WorkflowCandidateResponse,
    responses=READ
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_workflow_not_found, workflow_candidate_not_found",
        }
    },
    summary="A workflow candidate",
    description="Its architecture (canonical IR), the engines' reports, its lineage and the proposer's "
    "stated reasons and evidence.",
)
async def get_workflow_candidate(
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    workflow_candidate_id: uuid.UUID,
    current: CurrentUser,
    workflows: WorkflowServiceDep,
) -> WorkflowCandidateResponse:
    workflow, candidate = await workflows.candidate(
        project_id=project_id,
        workflow_id=workflow_id,
        candidate_id=workflow_candidate_id,
        user_id=current.user.id,
    )
    return WorkflowCandidateResponse.of(workflow, candidate)


@router.post(
    _WORKFLOW + "/input",
    response_model=WorkflowResponse,
    responses=WRITE
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_workflow_not_found, requirement_set_not_found",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Give the input the workflow waits for",
    description="The requirement set you confirmed (from the analysis it names), or answers to every "
    "blocking question. The workflow goes back to the queue and continues.",
)
async def provide_workflow_input(
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    body: WorkflowInputRequest,
    current: CurrentUser,
    workflows: WorkflowServiceDep,
    limits: RateLimitsDep,
) -> WorkflowResponse:
    await limits.enforce("run_architecture_workflow", user_id=current.user.id)
    await workflows.provide_input(
        project_id=project_id,
        workflow_id=workflow_id,
        user_id=current.user.id,
        requirement_set_id=body.requirement_set_id,
        answers=[(a.question_id, a.answer) for a in body.answers],
    )
    detail = await workflows.get(project_id=project_id, workflow_id=workflow_id, user_id=current.user.id)
    return WorkflowResponse.of(detail)


@router.post(
    _WORKFLOW + "/cancel",
    response_model=WorkflowResponse,
    responses=WRITE,
    summary="Cancel a workflow",
    description="Stops every future step — also of a running workflow, whose worker can no longer "
    "record anything. What was done is kept.",
)
async def cancel_architecture_workflow(
    project_id: uuid.UUID, workflow_id: uuid.UUID, current: CurrentUser, workflows: WorkflowServiceDep
) -> WorkflowResponse:
    await workflows.cancel(project_id=project_id, workflow_id=workflow_id, user_id=current.user.id)
    detail = await workflows.get(project_id=project_id, workflow_id=workflow_id, user_id=current.user.id)
    return WorkflowResponse.of(detail)


@router.post(
    _WORKFLOW + "/reject",
    response_model=WorkflowResponse,
    responses=WRITE,
    summary="Reject the review package",
    description="Records why; the workflow and its candidates are kept as they were reviewed.",
)
async def reject_architecture_workflow(
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    body: WorkflowRejectRequest,
    current: CurrentUser,
    workflows: WorkflowServiceDep,
) -> WorkflowResponse:
    await workflows.reject(
        project_id=project_id, workflow_id=workflow_id, user_id=current.user.id, reason=body.reason
    )
    detail = await workflows.get(project_id=project_id, workflow_id=workflow_id, user_id=current.user.id)
    return WorkflowResponse.of(detail)


@router.post(
    _WORKFLOW + "/approve",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkflowApprovedResponse,
    responses=WRITE
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_workflow_not_found, "
            "workflow_candidate_not_found, architecture_not_found",
        },
        409: {
            "model": ErrorResponse,
            "description": "workflow_candidate_not_approvable (details.reason: not_selected, "
            "candidate_changed, validation_blocking, validation_unknown, stale_candidate, no_change), "
            "invalid_workflow_transition, project_archived, architecture_archived",
        },
    },
    summary="Approve a candidate",
    description="Creates a new architecture — or, when the goal named a base, a new revision of it, which "
    "must still be its current revision (otherwise stale_candidate) — from exactly the reviewed "
    "candidate (its content hash), through the architecture workflow (source: ai). Refused unless "
    "validation evaluated it with no blocking findings. Needs architecture.generate and "
    "architecture.create or architecture.update. Earlier revisions never change.",
)
async def approve_workflow_candidate(
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    body: WorkflowApproveRequest,
    current: CurrentUser,
    workflows: WorkflowServiceDep,
) -> WorkflowApprovedResponse:
    approved = await workflows.approve(
        project_id=project_id,
        workflow_id=workflow_id,
        user_id=current.user.id,
        candidate_id=body.candidate_id,
        candidate_content_hash=body.candidate_content_hash,
        name=body.name,
    )
    return WorkflowApprovedResponse.of(approved)
