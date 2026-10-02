"""Discovery runs of a project: run a discovery over supplied artifacts (read, never executed), list
and read runs with their findings and proposal, record a person's decision about a candidate, accept
the reviewed proposal through the architecture workflow, compare runs (and a run with a baseline
revision), delete a run. Nothing here changes an architecture except accepting, explicitly."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import DiscoveryServiceDep, RateLimitsDep, get_clock
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.discovery import (
    AcceptedResponse,
    AcceptRequest,
    BaselineComparisonResponse,
    DecisionRequest,
    DiscoveryRunPage,
    DiscoveryRunRequest,
    DiscoveryRunResponse,
    DiscoveryRunSummary,
    FindingsResponse,
    ProposalResponse,
    ReviewResponse,
    RunComparisonResponse,
)
from core.domain.clock import Clock
from core.domain.discovery.runs import Baseline
from core.domain.discovery.values import RunStatus

router = APIRouter(tags=["discovery"])

_RUNS = "/projects/{project_id}/discovery-runs"
_RUN = _RUNS + "/{run_id}"

READ: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, discovery_run_not_found"},
}
WRITE = READ | {
    409: {"model": ErrorResponse, "description": "invalid_discovery_transition, project_archived"},
    422: {
        "model": ErrorResponse,
        "description": "invalid_discovery_request (details: field, reason), validation_error",
    },
}


@router.post(
    _RUNS,
    status_code=status.HTTP_201_CREATED,
    response_model=DiscoveryRunResponse,
    responses=WRITE
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, architecture_revision_not_found",
        },
        413: {"model": ErrorResponse, "description": "payload_too_large"},
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Run a discovery",
    description=(
        "Reads the supplied artifacts (Kubernetes manifests, Docker Compose, Terraform JSON, Architecture "
        "IR JSON) — never executing, evaluating, rendering or fetching anything — and stores the findings, "
        "candidates, relationships and a proposed architecture for review. Synchronous and bounded (50 "
        "artifacts, 512 KiB each, 2 MiB in all). A run the engine refuses for its limits is stored as "
        "failed. No architecture is changed."
    ),
)
async def run_discovery(
    project_id: uuid.UUID,
    body: DiscoveryRunRequest,
    current: CurrentUser,
    discoveries: DiscoveryServiceDep,
    limits: RateLimitsDep,
) -> DiscoveryRunResponse:
    await limits.enforce("run_discovery", user_id=current.user.id)
    run = await discoveries.run(project_id=project_id, user_id=current.user.id, request=body.to_domain())
    return DiscoveryRunResponse.of_run(run)


@router.get(
    _RUNS,
    response_model=DiscoveryRunPage,
    responses=READ | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Discovery runs of a project",
    description="Newest first, without their results; optionally of one status.",
)
async def list_discovery_runs(
    project_id: uuid.UUID,
    current: CurrentUser,
    discoveries: DiscoveryServiceDep,
    *,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DiscoveryRunPage:
    page = await discoveries.list_runs(
        project_id=project_id, user_id=current.user.id, status=run_status, cursor=cursor, limit=limit
    )
    runs = [DiscoveryRunSummary.of(r) for r in page.items]
    return DiscoveryRunPage(runs=runs, next_cursor=page.next_cursor)


@router.get(
    _RUN,
    response_model=DiscoveryRunResponse,
    responses=READ,
    summary="A discovery run",
    description="Its artifacts (never their content), candidates, relationships, diagnostics, proposal "
    "elements, validation, review decisions and acceptances. Findings: GET .../findings.",
)
async def get_discovery_run(
    project_id: uuid.UUID, run_id: uuid.UUID, current: CurrentUser, discoveries: DiscoveryServiceDep
) -> DiscoveryRunResponse:
    run = await discoveries.get(project_id=project_id, run_id=run_id, user_id=current.user.id)
    return DiscoveryRunResponse.of_run(run)


@router.get(
    _RUN + "/findings",
    response_model=FindingsResponse,
    responses=READ,
    summary="A discovery run's findings",
    description="Each extracted fact with where it was read and how it is known; secrets redacted.",
)
async def list_discovery_findings(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    current: CurrentUser,
    discoveries: DiscoveryServiceDep,
    entity: Annotated[str | None, Query(max_length=256)] = None,
) -> FindingsResponse:
    findings = await discoveries.findings(
        project_id=project_id, run_id=run_id, user_id=current.user.id, entity=entity
    )
    return FindingsResponse.of(run_id, findings)


@router.get(
    _RUN + "/proposal",
    response_model=ProposalResponse,
    responses=READ | {409: {"model": ErrorResponse, "description": "invalid_discovery_transition"}},
    summary="The reviewed proposal",
    description="The proposed architecture under the run's review decisions, computed now from the stored "
    "result, with what became of each candidate and the validation. Not an architecture until accepted.",
)
async def get_discovery_proposal(
    project_id: uuid.UUID, run_id: uuid.UUID, current: CurrentUser, discoveries: DiscoveryServiceDep
) -> ProposalResponse:
    run, proposal = await discoveries.proposal(project_id=project_id, run_id=run_id, user_id=current.user.id)
    return ProposalResponse.of(run.id, proposal)


@router.post(
    _RUN + "/decisions",
    response_model=ReviewResponse,
    responses=WRITE,
    summary="Decide about a candidate",
    description="Accept, reject or ignore a candidate entity or relationship; when accepting, choose an "
    "ambiguous mapping's component or state a kind the source does not establish (never overriding it).",
)
async def decide_discovery_candidate(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    body: DecisionRequest,
    current: CurrentUser,
    discoveries: DiscoveryServiceDep,
    clock: Annotated[Clock, Depends(get_clock)],
) -> ReviewResponse:
    decision = body.to_domain(current.user.id, clock())
    run = await discoveries.decide(
        project_id=project_id, run_id=run_id, user_id=current.user.id, decision=decision
    )
    return ReviewResponse.of(run)


@router.post(
    _RUN + "/accept",
    status_code=status.HTTP_201_CREATED,
    response_model=AcceptedResponse,
    responses=WRITE
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, discovery_run_not_found, architecture_not_found",
        },
        409: {
            "model": ErrorResponse,
            "description": "discovery_proposal_not_acceptable (details.reason), "
            "architecture_version_conflict, invalid_discovery_transition, project_archived, "
            "architecture_archived",
        },
    },
    summary="Accept the reviewed proposal",
    description="Creates a new architecture, or a new revision of an existing one based on its current "
    "revision, from exactly the reviewed proposal (its content hash), through the architecture workflow "
    "(source: discovery). Needs architecture.discover and architecture.create or architecture.update. "
    "Earlier revisions are never changed.",
)
async def accept_discovery_proposal(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    body: AcceptRequest,
    current: CurrentUser,
    discoveries: DiscoveryServiceDep,
) -> AcceptedResponse:
    accepted = await discoveries.accept(
        project_id=project_id,
        run_id=run_id,
        user_id=current.user.id,
        proposal_content_hash=body.proposal_content_hash,
        architecture_id=body.architecture_id,
        base_version=body.base_version,
        name=body.name,
    )
    return AcceptedResponse.of(accepted)


@router.get(
    _RUN + "/comparison",
    response_model=RunComparisonResponse,
    responses=READ | {409: {"model": ErrorResponse, "description": "invalid_discovery_transition"}},
    summary="Compare two discovery runs",
    description="The later run against the earlier, only on what both read alike: different extractor or "
    "rule versions make them not comparable (no difference is claimed). Differences are of what the "
    "sources declare, not runtime drift.",
)
async def compare_discovery_runs(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    current: CurrentUser,
    discoveries: DiscoveryServiceDep,
    other: Annotated[uuid.UUID, Query(alias="with")],
) -> RunComparisonResponse:
    comparison = await discoveries.compare(
        project_id=project_id, run_id=run_id, other_run_id=other, user_id=current.user.id
    )
    return RunComparisonResponse.of(comparison)


@router.get(
    _RUN + "/baseline-comparison",
    response_model=BaselineComparisonResponse,
    responses=READ
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, discovery_run_not_found, architecture_not_found, "
            "architecture_revision_not_found",
        },
        409: {"model": ErrorResponse, "description": "invalid_discovery_transition"},
        422: {"model": ErrorResponse, "description": "invalid_discovery_request, validation_error"},
    },
    summary="Compare a run with a baseline revision",
    description="The reviewed proposal against a revision (default: the run's baseline). What the baseline "
    "has and the sources do not describe is not_in_sources — unknown, never removed; nothing is matched "
    "by name.",
)
async def compare_discovery_with_baseline(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    current: CurrentUser,
    discoveries: DiscoveryServiceDep,
    architecture_id: Annotated[uuid.UUID | None, Query(alias="architectureId")] = None,
    revision: Annotated[int | None, Query(ge=1)] = None,
) -> BaselineComparisonResponse:
    baseline = Baseline(architecture_id, revision) if architecture_id and revision else None
    comparison = await discoveries.compare_with_baseline(
        project_id=project_id, run_id=run_id, user_id=current.user.id, baseline=baseline
    )
    return BaselineComparisonResponse.of(comparison)


@router.delete(
    _RUN,
    status_code=status.HTTP_204_NO_CONTENT,
    responses=READ
    | {409: {"model": ErrorResponse, "description": "accepted_discovery_run, project_archived"}},
    summary="Delete a discovery run",
    description="Removes the run and its findings. A run whose proposal was accepted is kept: it is the "
    "provenance of the revision it created.",
)
async def delete_discovery_run(
    project_id: uuid.UUID, run_id: uuid.UUID, current: CurrentUser, discoveries: DiscoveryServiceDep
) -> None:
    await discoveries.delete(project_id=project_id, run_id=run_id, user_id=current.user.id)
