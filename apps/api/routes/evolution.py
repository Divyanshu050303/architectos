"""Evolution analyses of an architecture: run one, list them, read one, page through its candidates,
read a candidate with its proposed diff, compare the alternatives per goal, and describe the rules.
Route handlers only translate HTTP to the service and back; the rules live in core/domain/evolution
and engines/evolution. Candidates are proposals for engineering review: nothing is applied."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import EvolutionServiceDep, RateLimitsDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.evolution import (
    AlternativesModel,
    AlternativesResponse,
    CandidateDetail,
    CandidateModel,
    CandidatePage,
    EvolutionAnalysisPage,
    EvolutionAnalysisResponse,
    EvolutionAnalysisSummary,
    EvolutionCatalog,
    RunEvolutionRequest,
)
from core.domain.evolution.queries import CandidateQuery
from core.domain.evolution.values import CandidateCategory, ValidationState

router = APIRouter(tags=["evolution"])

_ANALYSES = "/projects/{project_id}/architectures/{architecture_id}/evolution-analyses"
_ONE = _ANALYSES + "/{evolution_analysis_id}"
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_not_found"},
}
READ_ONE = ERRORS | {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, architecture_not_found, evolution_analysis_not_found",
    },
    422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"},
}


@router.post(
    _ANALYSES,
    status_code=status.HTTP_201_CREATED,
    response_model=EvolutionAnalysisResponse,
    responses=ERRORS
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, architecture_revision_not_found",
        },
        409: {"model": ErrorResponse, "description": "project_archived, architecture_archived"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_evolution_request (details: field, reason; e.g. unknown_analysis, "
            "unknown_requirement), validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Analyze how an architecture could evolve toward goals",
    description=(
        "Evaluates typed goals against an exact revision (default: the current one) from the stored "
        "analyses of the other engines — only those of the revision's exact content count; older ones are "
        "reported stale — and proposes configuration changes, each validated and evaluated by the engines "
        "that model it, with its trade-offs. Candidates are proposals for engineering review: nothing is "
        "applied, nothing is ranked, and no candidate is chosen. Synchronous."
    ),
)
async def run_evolution_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: RunEvolutionRequest,
    current: CurrentUser,
    evolution: EvolutionServiceDep,
    limits: RateLimitsDep,
) -> EvolutionAnalysisResponse:
    await limits.enforce("run_evolution_analysis", user_id=current.user.id)
    report = await evolution.analyze(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        goals=tuple(g.to_domain() for g in body.goals),
        revision_number=body.revision,
        requirement_ids=tuple(body.requirement_ids) if body.requirement_ids is not None else None,
        constraints=body.constraints.to_domain() if body.constraints is not None else None,
        scope=tuple(body.scope) if body.scope is not None else None,
        evidence=tuple(c.to_domain() for c in body.evidence),
        assumptions=body.assumption_values(),
        label=body.label,
    )
    return EvolutionAnalysisResponse.of(report)


@router.get(
    _ANALYSES,
    response_model=EvolutionAnalysisPage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Evolution analyses of an architecture",
    description="Newest first, optionally of one revision.",
)
async def list_evolution_analyses(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    evolution: EvolutionServiceDep,
    revision: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> EvolutionAnalysisPage:
    page = await evolution.list_analyses(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        revision=revision,
        cursor=cursor,
        limit=limit,
    )
    return EvolutionAnalysisPage(
        analyses=[EvolutionAnalysisSummary.of(r) for r in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _ONE,
    response_model=EvolutionAnalysisResponse,
    responses=READ_ONE,
    summary="An evolution analysis",
    description="Status, goals, findings (stale or missing evidence, unsupported goals, structural "
    "considerations, …), evidence considered, assumptions and limitations.",
)
async def get_evolution_analysis(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    evolution_analysis_id: uuid.UUID,
    current: CurrentUser,
    evolution: EvolutionServiceDep,
) -> EvolutionAnalysisResponse:
    report = await evolution.get(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=evolution_analysis_id,
        user_id=current.user.id,
    )
    return EvolutionAnalysisResponse.of(report)


@router.get(
    _ONE + "/candidates",
    response_model=CandidatePage,
    responses=READ_ONE,
    summary="The candidates of an evolution analysis",
    description="In canonical order (category, then id) — not a ranking: each with its changes, "
    "evidence, validation, impacts and trade-offs.",
)
async def list_evolution_candidates(  # noqa: PLR0913 -- one argument per filter
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    evolution_analysis_id: uuid.UUID,
    current: CurrentUser,
    evolution: EvolutionServiceDep,
    *,
    category: CandidateCategory | None = None,
    validation: ValidationState | None = None,
    goal: Annotated[str | None, Query(max_length=256)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CandidatePage:
    page = await evolution.list_candidates(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=evolution_analysis_id,
        user_id=current.user.id,
        query=CandidateQuery(category, validation, goal, limit=limit),
        cursor=cursor,
    )
    return CandidatePage(candidates=[CandidateModel.of(c) for c in page.items], next_cursor=page.next_cursor)


@router.get(
    _ONE + "/candidates/{candidate_id}",
    response_model=CandidateDetail,
    responses=READ_ONE
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, evolution_analysis_not_found, "
            "evolution_candidate_not_found",
        }
    },
    summary="A candidate and its proposed diff",
    description="The candidate, and its overlay re-applied to the exact baseline revision: each change "
    "(before -> after) and the IR's structural diff. Never applied to the architecture.",
)
async def get_evolution_candidate(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    evolution_analysis_id: uuid.UUID,
    candidate_id: Annotated[str, Path(pattern=r"^evo_[0-9a-f]{20}$")],
    current: CurrentUser,
    evolution: EvolutionServiceDep,
) -> CandidateDetail:
    candidate, overlay = await evolution.get_candidate(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=evolution_analysis_id,
        candidate_id=candidate_id,
        user_id=current.user.id,
    )
    return CandidateDetail(candidate=CandidateModel.of(candidate), overlay=dict(overlay) if overlay else None)


@router.get(
    _ONE + "/alternatives",
    response_model=AlternativesResponse,
    responses=READ_ONE,
    summary="The alternatives for each goal, side by side",
    description="For each goal, the candidates that address it and each one's direction per dimension "
    "(improves, worsens, mixed, unchanged, unknown, not_evaluated, consideration). No winner is chosen.",
)
async def get_evolution_alternatives(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    evolution_analysis_id: uuid.UUID,
    current: CurrentUser,
    evolution: EvolutionServiceDep,
) -> AlternativesResponse:
    found = await evolution.alternatives(
        project_id=project_id,
        architecture_id=architecture_id,
        analysis_id=evolution_analysis_id,
        user_id=current.user.id,
    )
    return AlternativesResponse(alternatives=[AlternativesModel.of(a) for a in found])


@router.get(
    "/evolution/catalog",
    response_model=EvolutionCatalog,
    responses={401: {"model": ErrorResponse}},
    summary="Supported goals, candidate categories, rules and limits",
    description="Each rule's contract: triggers, goals, required evidence, the properties it may change, "
    "preconditions, benefits, trade-offs, unsupported conditions, validation and analyses.",
)
async def get_evolution_catalog(current: CurrentUser, evolution: EvolutionServiceDep) -> EvolutionCatalog:
    return EvolutionCatalog.model_validate(evolution.catalog())
