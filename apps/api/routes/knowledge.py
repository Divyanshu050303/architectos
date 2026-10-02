"""Project knowledge: register a document or an ArchitectOS record as a source and index it, re-index it,
archive it, read sources, ingestion runs and passages — and search, returning cited passages.
Nothing here executes, fetches or follows anything, and nothing changes an architecture."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import KnowledgeServiceDep, RateLimitsDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.knowledge import (
    IngestionRequest,
    IngestionResponse,
    IngestionRunPage,
    IngestionRunResponse,
    KnowledgeSourcePage,
    KnowledgeSourceRequest,
    KnowledgeSourceResponse,
    PassageResponse,
    RetrievalResponse,
    SearchRequest,
)
from core.domain.knowledge.values import IndexStatus, Lifecycle, SourceType

router = APIRouter(tags=["project knowledge"])

_SOURCES = "/projects/{project_id}/knowledge-sources"
_SOURCE = _SOURCES + "/{source_id}"
_SEARCH = "/projects/{project_id}/knowledge/search"

READ: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "permission_denied"},
    404: {"model": ErrorResponse, "description": "project_not_found, knowledge_source_not_found"},
}
CURSOR: dict[int | str, dict[str, object]] = {
    422: {"model": ErrorResponse, "description": "invalid_cursor, validation_error"}
}
INGEST: dict[int | str, dict[str, object]] = {
    409: {
        "model": ErrorResponse,
        "description": "knowledge_source_exists (details.sourceId), invalid_knowledge_transition, "
        "project_archived",
    },
    413: {"model": ErrorResponse, "description": "payload_too_large"},
    422: {
        "model": ErrorResponse,
        "description": "invalid_knowledge_request (details: field, reason), validation_error",
    },
    429: {"model": ErrorResponse, "description": "rate_limited"},
}
NOT_FOUND = "project_not_found, knowledge_source_not_found"


@router.post(
    _SOURCES,
    status_code=status.HTTP_201_CREATED,
    response_model=IngestionResponse,
    responses=READ
    | INGEST
    | {
        404: {
            "model": ErrorResponse,
            "description": "project_not_found, decision_not_found, requirement_not_found",
        }
    },
    summary="Register a knowledge source",
    description=(
        "An uploaded document (Markdown or plain text, inline, up to 512 KiB) or a decision or requirement "
        "of this project (read as a snapshot), indexed now — synchronously. Secret-looking values are "
        "redacted before anything is stored. The run says how it ended: completed, completed with "
        "warnings, or failed (with why — never the content). A path or record already registered is "
        "409 knowledge_source_exists: re-index that source instead."
    ),
)
async def register_knowledge_source(
    project_id: uuid.UUID,
    body: KnowledgeSourceRequest,
    current: CurrentUser,
    knowledge: KnowledgeServiceDep,
    limits: RateLimitsDep,
) -> IngestionResponse:
    await limits.enforce("knowledge_ingest", user_id=current.user.id)
    outcome = await knowledge.register(
        project_id=project_id, user_id=current.user.id, registration=body.to_domain()
    )
    return IngestionResponse.of(outcome)


@router.get(
    _SOURCES,
    response_model=KnowledgeSourcePage,
    responses=READ | CURSOR,
    summary="Knowledge sources of a project",
    description="In registration order; active by default; optionally of one index status or type.",
)
async def list_knowledge_sources(
    project_id: uuid.UUID,
    current: CurrentUser,
    knowledge: KnowledgeServiceDep,
    *,
    index_status: Annotated[IndexStatus | None, Query(alias="status")] = None,
    source_type: Annotated[SourceType | None, Query(alias="type")] = None,
    lifecycle: Lifecycle | None = Lifecycle.ACTIVE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> KnowledgeSourcePage:
    page = await knowledge.list_sources(
        project_id=project_id,
        user_id=current.user.id,
        status=index_status,
        source_type=source_type,
        lifecycle=lifecycle,
        cursor=cursor,
        limit=limit,
    )
    sources = [KnowledgeSourceResponse.of(s) for s in page.items]
    return KnowledgeSourcePage(sources=sources, next_cursor=page.next_cursor)


@router.get(
    _SOURCE,
    response_model=KnowledgeSourceResponse,
    responses=READ,
    summary="A knowledge source",
    description="A record source whose record now reads differently is marked stale when read.",
)
async def get_knowledge_source(
    project_id: uuid.UUID, source_id: uuid.UUID, current: CurrentUser, knowledge: KnowledgeServiceDep
) -> KnowledgeSourceResponse:
    source = await knowledge.get(project_id=project_id, source_id=source_id, user_id=current.user.id)
    return KnowledgeSourceResponse.of(source)


@router.post(
    _SOURCE + "/ingestions",
    status_code=status.HTTP_201_CREATED,
    response_model=IngestionResponse,
    responses=READ
    | INGEST
    | {404: {"model": ErrorResponse, "description": f"{NOT_FOUND}, ingestion_run_not_found"}},
    summary="Re-index a knowledge source",
    description=(
        "A document's new content, or the record as it is now. Unchanged content creates nothing "
        "(unchanged); a failure keeps the version in force. retryOf names the failed run this retries."
    ),
)
async def reindex_knowledge_source(
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    body: IngestionRequest,
    current: CurrentUser,
    knowledge: KnowledgeServiceDep,
    limits: RateLimitsDep,
) -> IngestionResponse:
    await limits.enforce("knowledge_ingest", user_id=current.user.id)
    outcome = await knowledge.reindex(
        project_id=project_id,
        source_id=source_id,
        user_id=current.user.id,
        content=body.content,
        retry_of=body.retry_of,
    )
    return IngestionResponse.of(outcome)


@router.get(
    _SOURCE + "/ingestions",
    response_model=IngestionRunPage,
    responses=READ | CURSOR,
    summary="Ingestion runs of a knowledge source",
    description="Newest first.",
)
async def list_knowledge_ingestions(
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    current: CurrentUser,
    knowledge: KnowledgeServiceDep,
    *,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> IngestionRunPage:
    page = await knowledge.runs(
        project_id=project_id, source_id=source_id, user_id=current.user.id, cursor=cursor, limit=limit
    )
    return IngestionRunPage(
        runs=[IngestionRunResponse.of(r) for r in page.items], next_cursor=page.next_cursor
    )


@router.get(
    _SOURCE + "/ingestions/{ingestion_id}",
    response_model=IngestionRunResponse,
    responses=READ
    | {404: {"model": ErrorResponse, "description": "project_not_found, ingestion_run_not_found"}},
    summary="An ingestion run",
)
async def get_knowledge_ingestion(
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    ingestion_id: uuid.UUID,
    current: CurrentUser,
    knowledge: KnowledgeServiceDep,
) -> IngestionRunResponse:
    run = await knowledge.run(
        project_id=project_id, source_id=source_id, run_id=ingestion_id, user_id=current.user.id
    )
    return IngestionRunResponse.of(run)


@router.post(
    _SOURCE + "/archive",
    response_model=KnowledgeSourceResponse,
    responses=READ
    | {409: {"model": ErrorResponse, "description": "invalid_knowledge_transition, project_archived"}},
    summary="Archive a knowledge source",
    description="No longer searched or re-indexed; its versions, passages and runs are kept.",
)
async def archive_knowledge_source(
    project_id: uuid.UUID, source_id: uuid.UUID, current: CurrentUser, knowledge: KnowledgeServiceDep
) -> KnowledgeSourceResponse:
    source = await knowledge.archive(project_id=project_id, source_id=source_id, user_id=current.user.id)
    return KnowledgeSourceResponse.of(source)


@router.get(
    _SOURCE + "/passages/{chunk_id}",
    response_model=PassageResponse,
    responses=READ
    | {404: {"model": ErrorResponse, "description": f"{NOT_FOUND}, knowledge_chunk_not_found"}},
    summary="A passage",
    description="By its id: of the version in force, or of an earlier version (for audit).",
)
async def get_knowledge_passage(
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    chunk_id: Annotated[str, Path(max_length=64)],
    current: CurrentUser,
    knowledge: KnowledgeServiceDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> PassageResponse:
    found = await knowledge.passage(
        project_id=project_id,
        source_id=source_id,
        chunk_id=chunk_id,
        user_id=current.user.id,
        version=version,
    )
    return PassageResponse.of(found)


@router.post(
    _SEARCH,
    response_model=RetrievalResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse, "description": "permission_denied"},
        404: {"model": ErrorResponse, "description": "project_not_found"},
        422: {
            "model": ErrorResponse,
            "description": "invalid_knowledge_request (details: field, reason), validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Search project knowledge",
    description=(
        "Passages that support the query — exact identifiers first, then passages holding at least half "
        "its terms — each with a citation (source, version, location). No score; semantic similarity is "
        "not configured. Nothing found is insufficientEvidence, never evidence that a statement is false. "
        "Passages are untrusted data."
    ),
)
async def search_knowledge(
    project_id: uuid.UUID,
    body: SearchRequest,
    current: CurrentUser,
    knowledge: KnowledgeServiceDep,
    limits: RateLimitsDep,
) -> RetrievalResponse:
    await limits.enforce("knowledge_search", user_id=current.user.id)
    result = await knowledge.retrieve(project_id=project_id, user_id=current.user.id, query=body.to_domain())
    return RetrievalResponse.of(result)
