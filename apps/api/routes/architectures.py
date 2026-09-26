import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import ArchitectureServiceDep, RateLimitsDep
from apps.api.schemas.architecture import (
    ArchitectureComparison,
    ArchitectureResponse,
    CreateArchitectureRequest,
    DiffModel,
    EditArchitectureRequest,
    EditArchitectureResponse,
    RevisionSummaryModel,
    SaveLayoutRequest,
    VersionPage,
)
from apps.api.schemas.common import ErrorResponse
from core.architecture_ir.serialization import from_dict
from core.domain.architecture.entities import ArchitectureLayout
from core.domain.architecture.versions import RevisionSource
from core.domain.organizations.permissions import Permission

router = APIRouter(prefix="/projects/{project_id}/architecture", tags=["architecture"])

ARCHITECTURE_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_not_found"},
}
WRITE_ERRORS = ARCHITECTURE_ERRORS | {
    409: {"model": ErrorResponse, "description": "project_archived"},
    429: {"model": ErrorResponse, "description": "rate_limited"},
}
NOT_FOUND = "architecture_not_found, architecture_revision_not_found"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ArchitectureResponse,
    responses=WRITE_ERRORS
    | {
        404: {"model": ErrorResponse, "description": "project_not_found, requirement_set_not_found"},
        409: {"model": ErrorResponse, "description": "project_archived, architecture_already_exists"},
        413: {"model": ErrorResponse, "description": "payload_too_large"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_architecture (details.violations: element, elementId, field, rule, "
            "message), invalid_architecture_revision, validation_error",
        },
    },
    summary="Create the project's architecture",
    description=(
        "Starts the project's architecture at revision 1 from a complete Architecture IR document "
        "(designed here or imported). A project has one architecture; later changes are revisions."
    ),
)
async def create_architecture(
    project_id: uuid.UUID,
    body: CreateArchitectureRequest,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    limits: RateLimitsDep,
) -> ArchitectureResponse:
    await limits.enforce("create_architecture", user_id=current.user.id)
    # Authorize before parsing: a stranger must not make us validate a 2 MiB document.
    await architectures.authorize(
        project_id=project_id, user_id=current.user.id, permission=Permission.ARCHITECTURE_CREATE
    )
    architecture, revision = await architectures.create(
        project_id=project_id,
        user_id=current.user.id,
        ir=from_dict(body.ir),
        source=RevisionSource(body.source),
        reason=body.reason,
        requirement_set_id=body.requirement_set_id,
    )
    return ArchitectureResponse.build(architecture, revision, ArchitectureLayout())


@router.get(
    "",
    response_model=ArchitectureResponse,
    responses=ARCHITECTURE_ERRORS,
    summary="The project's architecture",
    description="The current revision, with the layout.",
)
async def get_architecture(
    project_id: uuid.UUID, current: CurrentUser, architectures: ArchitectureServiceDep
) -> ArchitectureResponse:
    architecture, revision, layout = await architectures.current(
        project_id=project_id, user_id=current.user.id
    )
    return ArchitectureResponse.build(architecture, revision, layout)


@router.get(
    "/versions",
    response_model=VersionPage,
    responses=ARCHITECTURE_ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor"}},
    summary="Revision history",
    description="Newest first, without the architecture content.",
)
async def list_architecture_versions(
    *,
    project_id: uuid.UUID,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> VersionPage:
    page = await architectures.history(
        project_id=project_id, user_id=current.user.id, cursor=cursor, limit=limit
    )
    return VersionPage(
        versions=[RevisionSummaryModel.of(r) for r in page.items], next_cursor=page.next_cursor
    )


@router.get(
    "/versions/{version}",
    response_model=ArchitectureResponse,
    responses=ARCHITECTURE_ERRORS | {404: {"model": ErrorResponse, "description": NOT_FOUND}},
    summary="One revision",
    description="Exactly as it was created; revisions never change.",
)
async def get_architecture_version(
    project_id: uuid.UUID, version: int, current: CurrentUser, architectures: ArchitectureServiceDep
) -> ArchitectureResponse:
    architecture, revision, layout = await architectures.version(
        project_id=project_id, user_id=current.user.id, number=version
    )
    return ArchitectureResponse.build(architecture, revision, layout)


@router.post(
    "/commands",
    status_code=status.HTTP_201_CREATED,
    response_model=EditArchitectureResponse,
    responses=WRITE_ERRORS
    | {
        409: {
            "model": ErrorResponse,
            "description": "project_archived, architecture_version_conflict (details.latestVersion)",
        },
        422: {
            "model": ErrorResponse,
            "description": "invalid_architecture_command (details: index, command, reason, elementId), "
            "invalid_architecture (details.violations), architecture_unchanged, validation_error",
        },
    },
    summary="Edit the architecture",
    description=(
        "Applies the edits, in order and all or nothing, to revision `baseVersion`, which must be the "
        "current one, and saves the result as a new revision. Every field an edit changes is attributed "
        "to the editor. The response includes what changed."
    ),
)
async def edit_architecture(
    project_id: uuid.UUID,
    body: EditArchitectureRequest,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    limits: RateLimitsDep,
) -> EditArchitectureResponse:
    await limits.enforce("edit_architecture", user_id=current.user.id)
    await architectures.authorize(
        project_id=project_id, user_id=current.user.id, permission=Permission.ARCHITECTURE_UPDATE
    )
    revised = await architectures.edit(
        project_id=project_id,
        user_id=current.user.id,
        base_version=body.base_version,
        commands=[command.to_domain() for command in body.commands],
        reason=body.reason,
    )
    return EditArchitectureResponse(
        **ArchitectureResponse.fields_for(revised.architecture, revised.revision, revised.layout),
        changes=DiffModel.of(revised.changes),
    )


@router.put(
    "/layout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=WRITE_ERRORS
    | {
        422: {
            "model": ErrorResponse,
            "description": "invalid_architecture_layout (details.reason, details.nodeId), validation_error",
        }
    },
    summary="Save the layout",
    description="Where each node is drawn. Never creates a revision.",
)
async def save_architecture_layout(
    project_id: uuid.UUID,
    body: SaveLayoutRequest,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    limits: RateLimitsDep,
) -> None:
    await limits.enforce("save_architecture_layout", user_id=current.user.id)
    await architectures.save_layout(
        project_id=project_id, user_id=current.user.id, positions=body.to_domain()
    )


@router.get(
    "/compare",
    response_model=ArchitectureComparison,
    responses=ARCHITECTURE_ERRORS | {404: {"model": ErrorResponse, "description": NOT_FOUND}},
    summary="Compare two revisions",
    description=(
        "A deterministic, field-level diff: elements are matched by id, so a rename is a modification. "
        "capacity and cost stay null until those engines exist."
    ),
)
async def compare_architecture_versions(
    *,
    project_id: uuid.UUID,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    from_version: Annotated[int, Query(alias="from", ge=1)],
    to_version: Annotated[int, Query(alias="to", ge=1)],
) -> ArchitectureComparison:
    before, after, changes = await architectures.compare(
        project_id=project_id, user_id=current.user.id, from_number=from_version, to_number=to_version
    )
    return ArchitectureComparison.compare(before, after, changes)
