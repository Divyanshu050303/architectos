"""Migration plans of a project: generate one between an exact source revision and an exact target,
list and read plans (with their staleness, computed on read), read a plan's ordered steps, risks,
checkpoints, rollback considerations and history, regenerate or revise it, and record a person's
review of an exact version. Nothing here executes a migration or changes the architecture."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import MigrationPlanServiceDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.migration import (
    ApprovePlanRequest,
    MigrationPlanHistory,
    MigrationPlanPage,
    MigrationPlanRequest,
    MigrationPlanResponse,
    MigrationPlanSummary,
    PlanCheckpointsResponse,
    PlanRisksResponse,
    PlanRollbacksResponse,
    PlanStepsResponse,
    RegeneratedPlanResponse,
    RegeneratePlanRequest,
    RejectPlanRequest,
)
from core.domain.migrations.values import PlanStatus

router = APIRouter(tags=["migration plans"])

_PLANS = "/projects/{project_id}/migration-plans"
_PLAN = _PLANS + "/{plan_id}"
_VERSION = _PLAN + "/versions/{version}"
type Version = Annotated[int, Path(ge=1, le=100_000)]

READ: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, architecture_not_found, migration_plan_not_found",
    },
}
GENERATE = READ | {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, architecture_not_found, architecture_revision_not_found, "
        "evolution_analysis_not_found, candidate_not_found, migration_plan_not_found",
    },
    409: {
        "model": ErrorResponse,
        "description": "reviewed_migration_plan, invalid_migration_plan_transition, project_archived, "
        "architecture_archived",
    },
    422: {
        "model": ErrorResponse,
        "description": "invalid_migration_request (details: field, reason), validation_error",
    },
}
REVIEW = READ | {
    409: {
        "model": ErrorResponse,
        "description": "invalid_migration_plan_transition, stale_migration_plan, "
        "migration_plan_version_mismatch, project_archived, architecture_archived",
    },
    422: {
        "model": ErrorResponse,
        "description": "invalid_migration_request (details: field, reason), validation_error",
    },
}


@router.post(
    _PLANS,
    status_code=status.HTTP_201_CREATED,
    response_model=MigrationPlanResponse,
    responses=GENERATE,
    summary="Generate a migration plan",
    description="Version 1 of a plan between an exact source revision and an exact target (a later revision "
    "of the same architecture, or an evolution candidate on the source revision): ordered steps with "
    "dependencies, risks, data migrations, downtime and compatibility, checkpoints, rollback considerations "
    "and the other engines' stored analyses as evidence. A proposal for review: nothing is executed.",
)
async def create_plan(
    project_id: uuid.UUID, body: MigrationPlanRequest, current: CurrentUser, plans: MigrationPlanServiceDep
) -> MigrationPlanResponse:
    view = await plans.create(project_id=project_id, user_id=current.user.id, request=body.to_domain())
    return MigrationPlanResponse.of_view(view)


@router.get(
    _PLANS,
    response_model=MigrationPlanPage,
    responses=READ | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Migration plans of a project",
    description="The latest version of each plan, newest first; optionally of one architecture or status.",
)
async def list_plans(
    project_id: uuid.UUID,
    current: CurrentUser,
    plans: MigrationPlanServiceDep,
    *,
    architecture_id: Annotated[uuid.UUID | None, Query(alias="architectureId")] = None,
    plan_status: Annotated[PlanStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MigrationPlanPage:
    page = await plans.list_plans(
        project_id=project_id,
        user_id=current.user.id,
        architecture_id=architecture_id,
        status=plan_status,
        cursor=cursor,
        limit=limit,
    )
    return MigrationPlanPage(
        plans=[MigrationPlanSummary.of(v) for v in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _PLAN,
    response_model=MigrationPlanResponse,
    responses=READ,
    summary="A migration plan's latest version",
    description="With its staleness, computed now. Reading changes nothing and implies no approval.",
)
async def get_plan(
    project_id: uuid.UUID, plan_id: uuid.UUID, current: CurrentUser, plans: MigrationPlanServiceDep
) -> MigrationPlanResponse:
    return MigrationPlanResponse.of_view(
        await plans.get(project_id=project_id, plan_id=plan_id, user_id=current.user.id)
    )


@router.get(
    _PLAN + "/versions",
    response_model=MigrationPlanHistory,
    responses=READ,
    summary="A migration plan's history",
    description="Every version with its review history (who, when, which status, the feedback).",
)
async def get_history(
    project_id: uuid.UUID, plan_id: uuid.UUID, current: CurrentUser, plans: MigrationPlanServiceDep
) -> MigrationPlanHistory:
    versions = await plans.history(project_id=project_id, plan_id=plan_id, user_id=current.user.id)
    return MigrationPlanHistory(versions=[MigrationPlanSummary.of(v) for v in versions])


@router.get(
    _VERSION, response_model=MigrationPlanResponse, responses=READ, summary="A version of a migration plan"
)
async def get_version(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    version: Version,
    current: CurrentUser,
    plans: MigrationPlanServiceDep,
) -> MigrationPlanResponse:
    return MigrationPlanResponse.of_view(
        await plans.get(project_id=project_id, plan_id=plan_id, user_id=current.user.id, version=version)
    )


@router.get(
    _VERSION + "/steps",
    response_model=PlanStepsResponse,
    responses=READ,
    summary="A version's steps and their order",
    description="Steps with their dependencies, and the sequence (stages; parallel only when explicit).",
)
async def get_steps(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    version: Version,
    current: CurrentUser,
    plans: MigrationPlanServiceDep,
) -> PlanStepsResponse:
    view = await plans.get(project_id=project_id, plan_id=plan_id, user_id=current.user.id, version=version)
    content = view.version.proposal.to_dict()
    return PlanStepsResponse.model_validate({"steps": content["steps"], "sequence": content["sequence"]})


@router.get(
    _VERSION + "/risks", response_model=PlanRisksResponse, responses=READ, summary="A version's risks"
)
async def get_risks(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    version: Version,
    current: CurrentUser,
    plans: MigrationPlanServiceDep,
) -> PlanRisksResponse:
    view = await plans.get(project_id=project_id, plan_id=plan_id, user_id=current.user.id, version=version)
    content = view.version.proposal.to_dict()
    return PlanRisksResponse.model_validate(
        {"risks": content["risks"], "assumptions": content["assumptions"]}
    )


@router.get(
    _VERSION + "/checkpoints",
    response_model=PlanCheckpointsResponse,
    responses=READ,
    summary="A version's verification checkpoints",
)
async def get_checkpoints(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    version: Version,
    current: CurrentUser,
    plans: MigrationPlanServiceDep,
) -> PlanCheckpointsResponse:
    view = await plans.get(project_id=project_id, plan_id=plan_id, user_id=current.user.id, version=version)
    return PlanCheckpointsResponse.model_validate(
        {"checkpoints": view.version.proposal.to_dict()["checkpoints"]}
    )


@router.get(
    _VERSION + "/rollbacks",
    response_model=PlanRollbacksResponse,
    responses=READ,
    summary="A version's rollback considerations",
)
async def get_rollbacks(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    version: Version,
    current: CurrentUser,
    plans: MigrationPlanServiceDep,
) -> PlanRollbacksResponse:
    view = await plans.get(project_id=project_id, plan_id=plan_id, user_id=current.user.id, version=version)
    return PlanRollbacksResponse.model_validate({"rollbacks": view.version.proposal.to_dict()["rollbacks"]})


@router.post(
    _PLAN + "/regenerate",
    response_model=RegeneratedPlanResponse,
    responses=GENERATE,
    summary="Regenerate or revise a migration plan",
    description="A new version from the latest version's request (or a revised request); the previous "
    "version is kept with its review history and superseded. A version under review or approved is "
    "replaced only with replaceReviewed; an identical result creates nothing.",
)
async def regenerate_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    body: RegeneratePlanRequest,
    current: CurrentUser,
    plans: MigrationPlanServiceDep,
) -> RegeneratedPlanResponse:
    view, created = await plans.regenerate(
        project_id=project_id,
        plan_id=plan_id,
        user_id=current.user.id,
        request=body.request.to_domain() if body.request is not None else None,
        replace_reviewed=body.replace_reviewed,
    )
    return RegeneratedPlanResponse(created=created, plan=MigrationPlanResponse.of_view(view))


@router.post(
    _VERSION + "/submit",
    response_model=MigrationPlanResponse,
    responses=REVIEW,
    summary="Submit a version for review",
    description="Only a current draft version.",
)
async def submit_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    version: Version,
    current: CurrentUser,
    plans: MigrationPlanServiceDep,
) -> MigrationPlanResponse:
    return MigrationPlanResponse.of_view(
        await plans.submit(project_id=project_id, plan_id=plan_id, version=version, user_id=current.user.id)
    )


@router.post(
    _VERSION + "/approve",
    response_model=MigrationPlanResponse,
    responses=REVIEW,
    summary="Approve an exact version",
    description="Needs migration.approve. The body names the fingerprint of the content reviewed; a stale "
    "version is refused. Approval executes nothing.",
)
async def approve_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    version: Version,
    body: ApprovePlanRequest,
    current: CurrentUser,
    plans: MigrationPlanServiceDep,
) -> MigrationPlanResponse:
    view = await plans.approve(
        project_id=project_id,
        plan_id=plan_id,
        version=version,
        user_id=current.user.id,
        fingerprint=body.fingerprint,
        comment=body.comment,
    )
    return MigrationPlanResponse.of_view(view)


@router.post(
    _VERSION + "/reject",
    response_model=MigrationPlanResponse,
    responses=REVIEW,
    summary="Reject an exact version",
    description="Needs migration.approve; the feedback is kept with the version.",
)
async def reject_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    version: Version,
    body: RejectPlanRequest,
    current: CurrentUser,
    plans: MigrationPlanServiceDep,
) -> MigrationPlanResponse:
    view = await plans.reject(
        project_id=project_id,
        plan_id=plan_id,
        version=version,
        user_id=current.user.id,
        fingerprint=body.fingerprint,
        comment=body.comment,
    )
    return MigrationPlanResponse.of_view(view)


@router.post(
    _VERSION + "/archive", response_model=MigrationPlanResponse, responses=REVIEW, summary="Archive a version"
)
async def archive_plan(
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    version: Version,
    current: CurrentUser,
    plans: MigrationPlanServiceDep,
) -> MigrationPlanResponse:
    return MigrationPlanResponse.of_view(
        await plans.archive(project_id=project_id, plan_id=plan_id, version=version, user_id=current.user.id)
    )
