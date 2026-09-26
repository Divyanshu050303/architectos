"""Validation runs of an architecture, their findings, and the rule catalog. Route handlers only
translate HTTP to the service and back; every rule lives in core/domain/validation and
engines/validation."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import RateLimitsDep, ValidationServiceDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.validation import (
    FindingModel,
    FindingPage,
    RuleCatalogResponse,
    RuleModel,
    RunValidationRequest,
    ValidationRunPage,
    ValidationRunResponse,
    ValidationRunSummary,
)
from core.domain.validation.queries import FindingQuery
from core.domain.validation.results import Category, Severity

router = APIRouter(tags=["validation"])

_RUNS = "/projects/{project_id}/architectures/{architecture_id}/validations"
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_not_found"},
}
RUN_NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, architecture_not_found, validation_run_not_found",
    },
    422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"},
}


@router.post(
    _RUNS,
    status_code=status.HTTP_201_CREATED,
    response_model=ValidationRunResponse,
    responses=ERRORS
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, architecture_revision_not_found",
        },
        409: {"model": ErrorResponse, "description": "project_archived, architecture_archived"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_validation_config (details: reason, ruleId, parameter), validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Validate an architecture revision",
    description=(
        "Runs the deterministic validation engine against a revision (default: the current one) "
        "with the project's requirements and architecture policy, and stores the run. Synchronous: "
        "the run is returned completed (findings are a successful validation) or failed (the "
        "engine could not produce a trustworthy result). Findings are read with GET …/findings."
    ),
)
async def run_validation(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: RunValidationRequest,
    current: CurrentUser,
    validations: ValidationServiceDep,
    limits: RateLimitsDep,
) -> ValidationRunResponse:
    await limits.enforce("run_validation", user_id=current.user.id)
    report = await validations.validate(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        config=body.to_config(),
        revision_number=body.revision,
    )
    return ValidationRunResponse.of(report)


@router.get(
    _RUNS,
    response_model=ValidationRunPage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Validation runs of an architecture",
    description="Newest first, optionally of one revision.",
)
async def list_validation_runs(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    validations: ValidationServiceDep,
    revision: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ValidationRunPage:
    page = await validations.list_runs(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        revision=revision,
        cursor=cursor,
        limit=limit,
    )
    return ValidationRunPage(
        runs=[ValidationRunSummary.of(r) for r in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _RUNS + "/{run_id}",
    response_model=ValidationRunResponse,
    responses=ERRORS | RUN_NOT_FOUND,
    summary="A validation run",
    description="Its summary, requirement verdicts, rule failures, limitations and inputs.",
)
async def get_validation_run(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    run_id: uuid.UUID,
    current: CurrentUser,
    validations: ValidationServiceDep,
) -> ValidationRunResponse:
    report = await validations.get_run(
        project_id=project_id, architecture_id=architecture_id, run_id=run_id, user_id=current.user.id
    )
    return ValidationRunResponse.of(report)


@router.get(
    _RUNS + "/{run_id}/findings",
    response_model=FindingPage,
    responses=ERRORS | RUN_NOT_FOUND,
    summary="Findings of a validation run",
    description="Most severe first (the run's canonical order), optionally filtered.",
)
async def list_validation_findings(  # noqa: PLR0913, PLR0917 -- one parameter per filter
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    run_id: uuid.UUID,
    current: CurrentUser,
    validations: ValidationServiceDep,
    severity: Severity | None = None,
    category: Category | None = None,
    blocking: bool | None = None,
    rule_id: Annotated[str | None, Query(alias="ruleId", min_length=3, max_length=64)] = None,
    entity_id: Annotated[str | None, Query(alias="entityId", min_length=1, max_length=256)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> FindingPage:
    page = await validations.list_findings(
        project_id=project_id,
        architecture_id=architecture_id,
        run_id=run_id,
        user_id=current.user.id,
        query=FindingQuery(severity, category, blocking, rule_id, entity_id, limit=limit),
        cursor=cursor,
    )
    return FindingPage(findings=[FindingModel.of(f) for f in page.items], next_cursor=page.next_cursor)


@router.get(
    "/validation/rules",
    response_model=RuleCatalogResponse,
    responses={401: {"model": ErrorResponse}},
    summary="The validation rules",
    description="Every rule the engine offers, by id, and the profiles that select them.",
)
async def list_validation_rules(
    current: CurrentUser, validations: ValidationServiceDep
) -> RuleCatalogResponse:
    rules = validations.rules()
    profiles = sorted({p for rule in rules for p in rule["profiles"]})
    return RuleCatalogResponse(rules=[RuleModel.of(r) for r in rules], profiles=profiles)
