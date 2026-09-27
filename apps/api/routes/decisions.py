"""Architecture decision records of a project: draft one from an evolution analysis, list and read
them, render one as an ADR, and record a person's decision (accept an option, reject them all,
supersede, link the revision that implements it). Nothing here changes the architecture."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import DecisionServiceDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.evolution import (
    AcceptDecisionRequest,
    DecisionDocument,
    DecisionPage,
    DecisionResponse,
    DraftDecisionRequest,
    LinkRevisionRequest,
    RejectDecisionRequest,
    SupersedeDecisionRequest,
)
from core.domain.decisions.entities import DecisionStatus

router = APIRouter(tags=["decisions"])

_DECISIONS = "/projects/{project_id}/decisions"
_ONE = _DECISIONS + "/{decision_id}"
ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, decision_not_found"},
}
CHANGE = ERRORS | {
    409: {
        "model": ErrorResponse,
        "description": "invalid_decision_transition, project_archived, architecture_archived",
    },
    422: {
        "model": ErrorResponse,
        "description": "invalid_decision (details: field, reason), validation_error",
    },
}


@router.post(
    _DECISIONS,
    status_code=status.HTTP_201_CREATED,
    response_model=DecisionResponse,
    responses=CHANGE
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, evolution_analysis_not_found",
        }
    },
    summary="Draft a decision record from an evolution analysis",
    description="A proposed ADR with the analysis's candidates (or those named) as options, its baseline, "
    "goals, evidence and assumptions. Nothing is decided and nothing is applied.",
)
async def draft_decision(
    project_id: uuid.UUID, body: DraftDecisionRequest, current: CurrentUser, decisions: DecisionServiceDep
) -> DecisionResponse:
    decision = await decisions.draft(
        project_id=project_id,
        architecture_id=body.architecture_id,
        analysis_id=body.analysis_id,
        user_id=current.user.id,
        candidate_ids=tuple(body.candidate_ids) if body.candidate_ids is not None else None,
        title=body.title,
    )
    return DecisionResponse.of(decision)


@router.get(
    _DECISIONS,
    response_model=DecisionPage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Decision records of a project",
    description="By number, optionally of one architecture or status.",
)
async def list_decisions(
    project_id: uuid.UUID,
    current: CurrentUser,
    decisions: DecisionServiceDep,
    *,
    architecture_id: Annotated[uuid.UUID | None, Query(alias="architectureId")] = None,
    decision_status: Annotated[DecisionStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DecisionPage:
    page = await decisions.list_decisions(
        project_id=project_id,
        user_id=current.user.id,
        architecture_id=architecture_id,
        status=decision_status,
        cursor=cursor,
        limit=limit,
    )
    return DecisionPage(decisions=[DecisionResponse.of(d) for d in page.items], next_cursor=page.next_cursor)


@router.get(_ONE, response_model=DecisionResponse, responses=ERRORS, summary="A decision record")
async def get_decision(
    project_id: uuid.UUID, decision_id: uuid.UUID, current: CurrentUser, decisions: DecisionServiceDep
) -> DecisionResponse:
    return DecisionResponse.of(
        await decisions.get(project_id=project_id, decision_id=decision_id, user_id=current.user.id)
    )


@router.get(
    _ONE + "/document",
    response_model=DecisionDocument,
    responses=ERRORS,
    summary="A decision record as an ADR document",
    description="Markdown: status, context, options with their trade-offs, the decision, consequences, "
    "evidence, assumptions and the resulting revision.",
)
async def get_decision_document(
    project_id: uuid.UUID, decision_id: uuid.UUID, current: CurrentUser, decisions: DecisionServiceDep
) -> DecisionDocument:
    decision = await decisions.get(project_id=project_id, decision_id=decision_id, user_id=current.user.id)
    markdown = await decisions.document(
        project_id=project_id, decision_id=decision_id, user_id=current.user.id
    )
    return DecisionDocument(reference=decision.reference, markdown=markdown)


@router.post(
    _ONE + "/accept",
    response_model=DecisionResponse,
    responses=CHANGE,
    summary="Accept one option",
    description="A person's decision, with a rationale. Accepting changes nothing in the architecture: "
    "applying the option is a separate, authorized change.",
)
async def accept_decision(
    project_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: AcceptDecisionRequest,
    current: CurrentUser,
    decisions: DecisionServiceDep,
) -> DecisionResponse:
    decision = await decisions.accept(
        project_id=project_id,
        decision_id=decision_id,
        user_id=current.user.id,
        candidate_id=body.candidate_id,
        rationale=body.rationale,
    )
    return DecisionResponse.of(decision)


@router.post(
    _ONE + "/reject", response_model=DecisionResponse, responses=CHANGE, summary="Reject every option"
)
async def reject_decision(
    project_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: RejectDecisionRequest,
    current: CurrentUser,
    decisions: DecisionServiceDep,
) -> DecisionResponse:
    decision = await decisions.reject(
        project_id=project_id, decision_id=decision_id, user_id=current.user.id, rationale=body.rationale
    )
    return DecisionResponse.of(decision)


@router.post(
    _ONE + "/supersede",
    response_model=DecisionResponse,
    responses=CHANGE,
    summary="Supersede an accepted decision",
    description="Replaced by another accepted decision of the same project.",
)
async def supersede_decision(
    project_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: SupersedeDecisionRequest,
    current: CurrentUser,
    decisions: DecisionServiceDep,
) -> DecisionResponse:
    decision = await decisions.supersede(
        project_id=project_id,
        decision_id=decision_id,
        user_id=current.user.id,
        by_decision_id=body.by_decision_id,
    )
    return DecisionResponse.of(decision)


@router.post(
    _ONE + "/resulting-revision",
    response_model=DecisionResponse,
    responses=CHANGE
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, decision_not_found, architecture_revision_not_found",
        }
    },
    summary="Link the revision that implements an accepted decision",
    description="A person's statement that a revision (made separately, after the baseline) implements "
    "the decision. Recorded as such: the engine never infers or verifies it.",
)
async def link_decision_revision(
    project_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: LinkRevisionRequest,
    current: CurrentUser,
    decisions: DecisionServiceDep,
) -> DecisionResponse:
    decision = await decisions.link_revision(
        project_id=project_id, decision_id=decision_id, user_id=current.user.id, revision_number=body.revision
    )
    return DecisionResponse.of(decision)
