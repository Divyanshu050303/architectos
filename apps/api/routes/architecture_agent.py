"""Architecture agent runs of a project: start a run against a requirement set, answer its questions,
cancel it, list and read runs, and — a person's decision — accept the candidate through the
architecture workflow or reject it. Nothing here changes an architecture except accepting,
explicitly."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import AgentServiceDep, RateLimitsDep
from apps.api.schemas.architecture_agent import (
    AcceptedResponse,
    AcceptRequest,
    AgentRunPage,
    AgentRunRequest,
    AgentRunResponse,
    AgentRunSummary,
    AnswersRequest,
    RejectRequest,
)
from apps.api.schemas.common import ErrorResponse
from core.domain.architecture_agent.values import RunStatus

router = APIRouter(tags=["architecture agent"])

_RUNS = "/projects/{project_id}/architecture-agent-runs"
_RUN = _RUNS + "/{run_id}"

READ: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, agent_run_not_found"},
}
WRITE = READ | {
    409: {"model": ErrorResponse, "description": "invalid_agent_transition, project_archived"},
    422: {
        "model": ErrorResponse,
        "description": "invalid_agent_request (details: field, reason), validation_error",
    },
}
RUNS_A_PASS = WRITE | {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, agent_run_not_found, requirement_set_not_found, "
        "architecture_not_found, architecture_revision_not_found",
    },
    429: {"model": ErrorResponse, "description": "rate_limited"},
}


@router.post(
    _RUNS,
    status_code=status.HTTP_201_CREATED,
    response_model=AgentRunResponse,
    responses=RUNS_A_PASS,
    summary="Start an architecture agent run",
    description=(
        "Designs against a requirement set in a fixed pipeline: the set's gaps (blocking ones make the run "
        "wait for answers), project knowledge through the retriever, one structured model proposal (at "
        "most one retry), a canonical-IR candidate built and checked deterministically, then the "
        "validation, reliability, security and observability engines. Synchronous and bounded by the "
        "run's budget. Without a configured model the run fails llm_unavailable. No architecture is "
        "changed: a person accepts or rejects the candidate."
    ),
)
async def start_agent_run(
    project_id: uuid.UUID,
    body: AgentRunRequest,
    current: CurrentUser,
    agent: AgentServiceDep,
    limits: RateLimitsDep,
) -> AgentRunResponse:
    request, budget = body.to_domain()
    await limits.enforce("run_architecture_agent", user_id=current.user.id)
    run = await agent.start(project_id=project_id, user_id=current.user.id, request=request, budget=budget)
    return AgentRunResponse.of(run)


@router.get(
    _RUNS,
    response_model=AgentRunPage,
    responses=READ | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Architecture agent runs of a project",
    description="Newest first, without their parts; optionally of one status.",
)
async def list_agent_runs(
    project_id: uuid.UUID,
    current: CurrentUser,
    agent: AgentServiceDep,
    *,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AgentRunPage:
    page = await agent.list(
        project_id=project_id, user_id=current.user.id, status=run_status, cursor=cursor, limit=limit
    )
    return AgentRunPage(runs=[AgentRunSummary.of(r) for r in page.items], next_cursor=page.next_cursor)


@router.get(
    _RUN,
    response_model=AgentRunResponse,
    responses=READ,
    summary="An architecture agent run",
    description="Its questions and answers, the validated proposal (every claim with its basis), the "
    "candidate (canonical IR), the engines' reports, what was used, and the decision. Never the prompt, "
    "the retrieved text or the model's raw output.",
)
async def get_agent_run(
    project_id: uuid.UUID, run_id: uuid.UUID, current: CurrentUser, agent: AgentServiceDep
) -> AgentRunResponse:
    run = await agent.get(project_id=project_id, run_id=run_id, user_id=current.user.id)
    return AgentRunResponse.of(run)


@router.post(
    _RUN + "/answers",
    response_model=AgentRunResponse,
    responses=RUNS_A_PASS,
    summary="Answer the run's questions",
    description="Once every blocking question is answered, the same run continues with the answers as "
    "your statements (another pass: it may call the model).",
)
async def answer_agent_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    body: AnswersRequest,
    current: CurrentUser,
    agent: AgentServiceDep,
    limits: RateLimitsDep,
) -> AgentRunResponse:
    await limits.enforce("run_architecture_agent", user_id=current.user.id)
    answers = [(a.question_id, a.answer) for a in body.answers]
    run = await agent.answer(project_id=project_id, run_id=run_id, user_id=current.user.id, answers=answers)
    return AgentRunResponse.of(run)


@router.post(
    _RUN + "/cancel",
    response_model=AgentRunResponse,
    responses=WRITE,
    summary="Cancel a run waiting for answers",
    description="Only a run awaiting clarification can be cancelled; it is kept, as cancelled.",
)
async def cancel_agent_run(
    project_id: uuid.UUID, run_id: uuid.UUID, current: CurrentUser, agent: AgentServiceDep
) -> AgentRunResponse:
    run = await agent.cancel(project_id=project_id, run_id=run_id, user_id=current.user.id)
    return AgentRunResponse.of(run)


@router.post(
    _RUN + "/reject",
    response_model=AgentRunResponse,
    responses=WRITE,
    summary="Reject the candidate",
    description="Records why; the run is kept as it was reviewed.",
)
async def reject_agent_candidate(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    body: RejectRequest,
    current: CurrentUser,
    agent: AgentServiceDep,
) -> AgentRunResponse:
    run = await agent.reject(
        project_id=project_id, run_id=run_id, user_id=current.user.id, reason=body.reason
    )
    return AgentRunResponse.of(run)


@router.post(
    _RUN + "/accept",
    status_code=status.HTTP_201_CREATED,
    response_model=AcceptedResponse,
    responses=WRITE
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, agent_run_not_found, architecture_not_found",
        },
        409: {
            "model": ErrorResponse,
            "description": "agent_candidate_not_acceptable (details.reason: candidate_changed, "
            "validation_blocking, validation_unknown, validation_missing, no_change), "
            "architecture_version_conflict, invalid_agent_transition, project_archived, "
            "architecture_archived",
        },
    },
    summary="Accept the candidate",
    description="Creates a new architecture — or, for an iteration, a new revision of the base, which "
    "must still be its current revision — from exactly the reviewed candidate (its content hash), "
    "through the architecture workflow (source: ai). Refused while validation reports blocking findings. "
    "Needs architecture.generate and architecture.create or architecture.update. Earlier revisions "
    "never change.",
)
async def accept_agent_candidate(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    body: AcceptRequest,
    current: CurrentUser,
    agent: AgentServiceDep,
) -> AcceptedResponse:
    accepted = await agent.accept(
        project_id=project_id,
        run_id=run_id,
        user_id=current.user.id,
        candidate_content_hash=body.candidate_content_hash,
        name=body.name,
    )
    return AcceptedResponse.of(accepted)
