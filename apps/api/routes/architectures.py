"""The architectures of a project: metadata, lifecycle, content revisions, history, comparison,
restore and layout. Route handlers only translate HTTP to the service and back; every rule lives in
core/domain/architecture."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import ArchitectureServiceDep, RateLimitsDep
from apps.api.schemas.architecture import (
    ArchitectureComparison,
    ArchitecturePage,
    ArchitectureResponse,
    ArchitectureSummary,
    CreateArchitectureRequest,
    DiffModel,
    EditArchitectureRequest,
    HistoryItem,
    ReplaceContentRequest,
    RestoreRevisionRequest,
    RevisedResponse,
    RevisionModel,
    SaveLayoutRequest,
    UpdateArchitectureRequest,
    VersionPage,
)
from apps.api.schemas.common import ErrorResponse
from core.architecture_ir.serialization import from_dict
from core.domain.architecture.architecture_service import Revised
from core.domain.architecture.entities import ArchitectureLayout, ArchitectureStatus
from core.domain.architecture.versions import RevisionSource
from core.domain.organizations.permissions import Permission

router = APIRouter(prefix="/projects/{project_id}/architectures", tags=["architectures"])

ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, architecture_not_found"},
}
REVISION_NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {
        "model": ErrorResponse,
        "description": "project_not_found, architecture_not_found, architecture_revision_not_found",
    }
}
WRITE = ERRORS | {
    409: {"model": ErrorResponse, "description": "project_archived, architecture_archived"},
    429: {"model": ErrorResponse, "description": "rate_limited"},
}
CONTENT = WRITE | {
    409: {
        "model": ErrorResponse,
        "description": "project_archived, architecture_archived, "
        "architecture_version_conflict (details.latestVersion)",
    },
    422: {
        "model": ErrorResponse,
        "description": "invalid_architecture (details.violations: element, elementId, field, rule, "
        "message), invalid_architecture_command (details: index, command, reason, elementId), "
        "invalid_architecture_revision, validation_error",
    },
}


def _revised(revised: Revised, response: Response) -> RevisedResponse:
    """201 when a revision was created; 200 with the unchanged current revision otherwise."""
    if not revised.created:
        response.status_code = status.HTTP_200_OK
    return RevisedResponse(
        **ArchitectureResponse.fields_for(revised.architecture, revised.revision, revised.layout),
        created=revised.created,
        changes=DiffModel.of(revised.changes),
    )


# --- architectures ---------------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ArchitectureResponse,
    responses=WRITE
    | {
        404: {"model": ErrorResponse, "description": "project_not_found, requirement_set_not_found"},
        409: {"model": ErrorResponse, "description": "project_archived, architecture_name_taken"},
        413: {"model": ErrorResponse, "description": "payload_too_large"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_architecture (details.violations), invalid_architecture_metadata "
            "(details: field, reason), invalid_architecture_revision, validation_error",
        },
    },
    summary="Create an architecture",
    description=(
        "A new architecture of the project, at revision 1: the given IR document (designed here or "
        "imported), or an empty architecture when `ir` is omitted. Names are unique among the "
        "project's live architectures, ignoring case."
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
        name=body.name,
        description=body.description,
        ir=from_dict(body.ir) if body.ir is not None else None,
        source=RevisionSource(body.source),
        reason=body.reason,
        requirement_set_id=body.requirement_set_id,
    )
    return ArchitectureResponse.build(architecture, revision, ArchitectureLayout())


@router.get(
    "",
    response_model=ArchitecturePage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}},
    summary="Architectures of a project",
    description="Newest first; optionally filtered by status and by a case-insensitive name search.",
)
async def list_architectures(
    *,
    project_id: uuid.UUID,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    status_filter: Annotated[ArchitectureStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ArchitecturePage:
    page = await architectures.list(
        project_id=project_id,
        user_id=current.user.id,
        status=status_filter,
        search=search,
        cursor=cursor,
        limit=limit,
    )
    return ArchitecturePage(
        architectures=[ArchitectureSummary.of(a) for a in page.items], next_cursor=page.next_cursor
    )


@router.get(
    "/{architecture_id}",
    response_model=ArchitectureResponse,
    responses=ERRORS,
    summary="An architecture",
    description="Metadata, the current revision's content and the layout.",
)
async def get_architecture(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
) -> ArchitectureResponse:
    architecture, revision, layout = await architectures.get(
        project_id=project_id, architecture_id=architecture_id, user_id=current.user.id
    )
    return ArchitectureResponse.build(architecture, revision, layout)


@router.patch(
    "/{architecture_id}",
    response_model=ArchitectureSummary,
    responses=WRITE
    | {
        409: {
            "model": ErrorResponse,
            "description": "project_archived, architecture_archived, architecture_name_taken",
        },
        422: {
            "model": ErrorResponse,
            "description": "invalid_architecture_metadata, nothing_to_update, validation_error",
        },
    },
    summary="Update an architecture's name or description",
    description="Metadata only: creates no revision (the content is changed through revisions).",
)
async def update_architecture(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: UpdateArchitectureRequest,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    limits: RateLimitsDep,
) -> ArchitectureSummary:
    await limits.enforce("edit_architecture", user_id=current.user.id)
    updated = await architectures.update_metadata(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        name=body.name,
        description=body.description,
    )
    return ArchitectureSummary.of(updated)


@router.post(
    "/{architecture_id}/archive",
    response_model=ArchitectureSummary,
    responses=WRITE,
    summary="Archive an architecture",
    description="Read-only until restored; idempotent.",
)
async def archive_architecture(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
) -> ArchitectureSummary:
    archived = await architectures.archive(
        project_id=project_id, architecture_id=architecture_id, user_id=current.user.id
    )
    return ArchitectureSummary.of(archived)


@router.post(
    "/{architecture_id}/restore",
    response_model=ArchitectureSummary,
    responses=WRITE,
    summary="Restore an archived architecture",
    description="Back from the archive; idempotent. (To bring back older content, restore a revision.)",
)
async def restore_architecture(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
) -> ArchitectureSummary:
    restored = await architectures.restore(
        project_id=project_id, architecture_id=architecture_id, user_id=current.user.id
    )
    return ArchitectureSummary.of(restored)


@router.delete(
    "/{architecture_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=WRITE
    | {409: {"model": ErrorResponse, "description": "project_archived, architecture_not_archived"}},
    summary="Delete an archived architecture",
    description=(
        "Owners and admins, archived architectures only (archive first). A soft delete: the "
        "architecture is no longer found anywhere; its revisions are kept."
    ),
)
async def delete_architecture(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
) -> None:
    await architectures.delete(
        project_id=project_id, architecture_id=architecture_id, user_id=current.user.id
    )


# --- content ---------------------------------------------------------------------------------------


@router.put(
    "/{architecture_id}/content",
    status_code=status.HTTP_201_CREATED,
    response_model=RevisedResponse,
    responses=CONTENT
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, architecture_not_found, requirement_set_not_found",
        },
        413: {"model": ErrorResponse, "description": "payload_too_large"},
    },
    summary="Save new content",
    description=(
        "The whole IR as a new revision, based on revision `baseVersion`, which must be current. "
        "Content equal to the current revision creates nothing: 200 with the current revision and "
        "`created: false`."
    ),
)
async def replace_architecture_content(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: ReplaceContentRequest,
    response: Response,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    limits: RateLimitsDep,
) -> RevisedResponse:
    await limits.enforce("edit_architecture", user_id=current.user.id)
    await architectures.authorize(
        project_id=project_id, user_id=current.user.id, permission=Permission.ARCHITECTURE_UPDATE
    )
    revised = await architectures.replace(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        base_version=body.base_version,
        ir=from_dict(body.ir),
        source=RevisionSource(body.source),
        reason=body.reason,
        requirement_set_id=body.requirement_set_id,
    )
    return _revised(revised, response)


@router.post(
    "/{architecture_id}/commands",
    status_code=status.HTTP_201_CREATED,
    response_model=RevisedResponse,
    responses=CONTENT,
    summary="Edit the content",
    description=(
        "Applies the edits, in order and all or nothing, to revision `baseVersion`, which must be "
        "current, and saves the result as a new revision. Every field an edit changes is attributed "
        "to the editor. Edits that change nothing create nothing (200, `created: false`)."
    ),
)
async def edit_architecture(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    body: EditArchitectureRequest,
    response: Response,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    limits: RateLimitsDep,
) -> RevisedResponse:
    await limits.enforce("edit_architecture", user_id=current.user.id)
    await architectures.authorize(
        project_id=project_id, user_id=current.user.id, permission=Permission.ARCHITECTURE_UPDATE
    )
    revised = await architectures.edit(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        base_version=body.base_version,
        commands=[command.to_domain() for command in body.commands],
        reason=body.reason,
    )
    return _revised(revised, response)


@router.put(
    "/{architecture_id}/layout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=WRITE
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
    architecture_id: uuid.UUID,
    body: SaveLayoutRequest,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    limits: RateLimitsDep,
) -> None:
    await limits.enforce("save_architecture_layout", user_id=current.user.id)
    await architectures.save_layout(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        positions=body.to_domain(),
    )


# --- history ---------------------------------------------------------------------------------------


@router.get(
    "/{architecture_id}/versions",
    response_model=VersionPage,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "invalid_cursor"}},
    summary="Revision history",
    description="Newest first, without the content; `current` marks the current revision.",
)
async def list_architecture_versions(
    *,
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> VersionPage:
    history = await architectures.history(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        cursor=cursor,
        limit=limit,
    )
    now = history.architecture.current_revision
    return VersionPage(
        versions=[
            HistoryItem(**RevisionModel.fields_of(r), current=r.number == now) for r in history.page.items
        ],
        next_cursor=history.page.next_cursor,
    )


@router.get(
    "/{architecture_id}/versions/{version}",
    response_model=ArchitectureResponse,
    responses=ERRORS | REVISION_NOT_FOUND,
    summary="One revision",
    description="Exactly as stored, in the IR schema version it was stored in; revisions never change.",
)
async def get_architecture_version(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    version: int,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
) -> ArchitectureResponse:
    architecture, revision, layout = await architectures.version(
        project_id=project_id, architecture_id=architecture_id, user_id=current.user.id, number=version
    )
    return ArchitectureResponse.build(architecture, revision, layout)


@router.post(
    "/{architecture_id}/versions/{version}/restore",
    status_code=status.HTTP_201_CREATED,
    response_model=RevisedResponse,
    responses=CONTENT | REVISION_NOT_FOUND,
    summary="Restore a revision",
    description=(
        "A new revision (after the current one, `baseVersion`) whose content is revision `version`'s. "
        "Nothing is erased or moved back: every revision in between is kept, and the new one records "
        "`restoredFromVersion`. Restoring content equal to the current one creates nothing (200)."
    ),
)
async def restore_architecture_version(
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    version: int,
    body: RestoreRevisionRequest,
    response: Response,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    limits: RateLimitsDep,
) -> RevisedResponse:
    await limits.enforce("edit_architecture", user_id=current.user.id)
    revised = await architectures.restore_revision(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        number=version,
        base_version=body.base_version,
        reason=body.reason,
    )
    return _revised(revised, response)


@router.get(
    "/{architecture_id}/compare",
    response_model=ArchitectureComparison,
    responses=ERRORS | REVISION_NOT_FOUND,
    summary="Compare two revisions",
    description=(
        "A deterministic, field-level diff between two revisions of this architecture: elements are "
        "matched by id, so a rename is a modification. Secret-looking values are redacted. "
        "capacity and cost stay null until those engines exist."
    ),
)
async def compare_architecture_versions(
    *,
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    current: CurrentUser,
    architectures: ArchitectureServiceDep,
    from_version: Annotated[int, Query(alias="from", ge=1)],
    to_version: Annotated[int, Query(alias="to", ge=1)],
) -> ArchitectureComparison:
    before, after, changes = await architectures.compare(
        project_id=project_id,
        architecture_id=architecture_id,
        user_id=current.user.id,
        from_number=from_version,
        to_number=to_version,
    )
    return ArchitectureComparison.compare(before, after, changes)
