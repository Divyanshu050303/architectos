"""Maps every failure to the one error contract: {"error": {code, message, details, request_id}}.

Nothing here echoes request input back (FastAPI's default validation errors include the raw
input, which would put passwords in responses and logs), and unexpected errors never expose
internals: the client gets a generic message and the request id to quote.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic.alias_generators import to_camel
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.access_tokens import AccessTokenExpired, InvalidAccessToken, Unauthenticated
from apps.api.dependencies.auth import CsrfRejected
from apps.api.middleware.logging import route_template
from apps.api.middleware.rate_limit import RateLimited
from apps.api.middleware.request_id import HEADER as REQUEST_ID_HEADER
from apps.api.middleware.request_id import current_request_id
from apps.api.middleware.security_headers import security_headers
from core.architecture_ir.commands import InvalidArchitectureCommand
from core.architecture_ir.errors import InvalidArchitecture
from core.domain.architecture.errors import (
    ArchitectureArchived,
    ArchitectureNameTaken,
    ArchitectureNotArchived,
    ArchitectureNotFound,
    ArchitectureRevisionNotFound,
    ArchitectureUnchanged,
    ArchitectureVersionConflict,
    InvalidArchitectureMetadata,
    InvalidLayout,
    InvalidRevision,
)
from core.domain.audit.errors import InvalidCursor
from core.domain.capacity.errors import (
    CapacityAnalysisNotFound,
    InvalidCapacityConfig,
    InvalidQuantity,
    InvalidScenario,
    InvalidWorkload,
)
from core.domain.cost.errors import (
    CostAnalysisNotFound,
    IncompatibleCapacityAnalysis,
    InvalidCostRequest,
    InvalidMoney,
    InvalidPricingRecord,
    InvalidPricingSnapshot,
    PricingSnapshotNotFound,
)
from core.domain.errors import DomainError
from core.domain.identity.errors import (
    AccountDisabled,
    IncorrectPassword,
    InvalidAvatarUrl,
    InvalidCredentials,
    InvalidEmail,
    InvalidName,
    InvalidRefreshToken,
    InvalidToken,
    NothingToUpdate,
    RefreshConflict,
    SessionExpired,
    SessionNotFound,
    SessionRevoked,
    TokenExpired,
    WeakPassword,
)
from core.domain.organizations.errors import (
    AlreadyMember,
    CannotChangeOwnRole,
    EmailNotVerified,
    InvalidInvitation,
    InvalidOrganizationName,
    InvitationEmailMismatch,
    InvitationExpired,
    InvitationNotFound,
    LastOwner,
    MemberNotFound,
    OrganizationNotFound,
    OwnerInvitationNotAllowed,
    PermissionDenied,
    RoleNotManageable,
    SoleOwnerOfOrganization,
)
from core.domain.projects.errors import (
    InvalidArchitecturePolicy,
    InvalidProjectDescription,
    InvalidProjectName,
    InvalidProjectSettings,
    InvalidProjectSlug,
    ProjectArchived,
    ProjectNotArchived,
    ProjectNotFound,
    ProjectSlugTaken,
)
from core.domain.requirements.errors import (
    CandidateAlreadyPromoted,
    ChangeReasonRequired,
    InvalidPromotion,
    InvalidRequirement,
    InvalidRequirementInput,
    InvalidRequirementSet,
    InvalidStatusTransition,
    RequirementAnalysisNotFound,
    RequirementLocked,
    RequirementNotFound,
    RequirementSetConflicts,
    RequirementSetNotFound,
    RequirementVersionConflict,
    RequirementVersionNotFound,
)
from core.domain.validation.errors import InvalidValidationConfig, ValidationRunNotFound

logger = logging.getLogger("architectos.api")
security_log = logging.getLogger("architectos.security")

# Refusals worth observing (authentication, authorization, abuse). Logged with the error code and
# route template only: no identifiers, tokens or personal data.
_OBSERVED_STATUSES = {401, 403, 409, 429}

# Domain error -> HTTP status. Errors not listed are 400.
STATUS_BY_ERROR: dict[type[DomainError], int] = {
    InvalidEmail: 422,
    InvalidName: 422,
    WeakPassword: 422,
    InvalidToken: 400,
    TokenExpired: 400,
    InvalidCredentials: 401,
    AccountDisabled: 403,
    InvalidRefreshToken: 401,
    SessionExpired: 401,
    RefreshConflict: 409,
    SessionRevoked: 401,
    Unauthenticated: 401,
    InvalidAccessToken: 401,
    AccessTokenExpired: 401,
    CsrfRejected: 403,
    SessionNotFound: 404,
    IncorrectPassword: 400,
    InvalidAvatarUrl: 422,
    NothingToUpdate: 422,
    OrganizationNotFound: 404,
    PermissionDenied: 403,
    EmailNotVerified: 403,
    InvalidOrganizationName: 422,
    MemberNotFound: 404,
    CannotChangeOwnRole: 403,
    RoleNotManageable: 403,
    LastOwner: 409,
    SoleOwnerOfOrganization: 409,
    InvitationNotFound: 404,
    InvalidInvitation: 404,
    InvitationExpired: 410,
    InvitationEmailMismatch: 403,
    AlreadyMember: 409,
    OwnerInvitationNotAllowed: 422,
    InvalidCursor: 422,
    RateLimited: 429,
    ProjectNotFound: 404,
    ProjectArchived: 409,
    ProjectNotArchived: 409,
    ProjectSlugTaken: 409,
    InvalidProjectName: 422,
    InvalidProjectDescription: 422,
    InvalidProjectSlug: 422,
    InvalidProjectSettings: 422,
    RequirementNotFound: 404,
    RequirementVersionNotFound: 404,
    InvalidRequirement: 422,
    ChangeReasonRequired: 422,
    RequirementVersionConflict: 409,
    InvalidStatusTransition: 409,
    RequirementLocked: 409,
    RequirementSetNotFound: 404,
    InvalidRequirementSet: 422,
    RequirementSetConflicts: 409,
    InvalidRequirementInput: 422,
    RequirementAnalysisNotFound: 404,
    CandidateAlreadyPromoted: 409,
    InvalidPromotion: 422,
    InvalidArchitecture: 422,
    InvalidArchitectureCommand: 422,
    ArchitectureNotFound: 404,
    ArchitectureRevisionNotFound: 404,
    ArchitectureNameTaken: 409,
    ArchitectureArchived: 409,
    ArchitectureNotArchived: 409,
    InvalidArchitectureMetadata: 422,
    ArchitectureVersionConflict: 409,
    ArchitectureUnchanged: 422,
    InvalidRevision: 422,
    InvalidLayout: 422,
    InvalidArchitecturePolicy: 422,
    InvalidValidationConfig: 422,
    ValidationRunNotFound: 404,
    InvalidWorkload: 422,
    InvalidQuantity: 422,
    InvalidCapacityConfig: 422,
    InvalidScenario: 422,
    CapacityAnalysisNotFound: 404,
    InvalidMoney: 422,
    InvalidPricingRecord: 422,
    InvalidPricingSnapshot: 422,
    PricingSnapshotNotFound: 404,
    InvalidCostRequest: 422,
    IncompatibleCapacityAnalysis: 422,
    CostAnalysisNotFound: 404,
}

# RFC 6750: 401s for Bearer-protected resources say how to authenticate.
BEARER_CHALLENGES: dict[type[DomainError], str] = {
    Unauthenticated: 'Bearer realm="architectos"',
    InvalidAccessToken: 'Bearer realm="architectos", error="invalid_token"',
    AccessTokenExpired: 'Bearer realm="architectos", error="invalid_token", error_description="expired"',
    SessionRevoked: 'Bearer realm="architectos", error="invalid_token"',
}

HTTP_CODES: dict[int, tuple[str, str]] = {
    # FastAPI answers bodies it cannot parse (e.g. JSON nested past the parser's limit) with 400.
    400: ("malformed_request", "The request body could not be parsed."),
    404: ("not_found", "Not found."),
    405: ("method_not_allowed", "This method is not allowed here."),
}


def error_response(
    status: int, code: str, message: str, details: Any = None, *, headers: dict[str, str] | None = None
) -> JSONResponse:
    request_id = current_request_id()
    body = {"error": {"code": code, "message": message, "details": details, "request_id": request_id}}
    all_headers = dict(headers or {})
    if request_id:
        all_headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(status_code=status, content=body, headers=all_headers or None)


def domain_error_response(error: DomainError) -> JSONResponse:
    challenge = BEARER_CHALLENGES.get(type(error))
    headers = {"WWW-Authenticate": challenge} if challenge else {}
    if isinstance(error, RateLimited):
        headers["Retry-After"] = str(error.retry_after)
    return error_response(
        _status_for(error), error.code, error.detail_message, _camel_details(error.details), headers=headers
    )


def _camel_keys(value: Any) -> Any:
    """Object keys in camelCase at any depth (values untouched)."""
    if isinstance(value, dict):
        return {
            to_camel(key) if isinstance(key, str) else key: _camel_keys(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_camel_keys(item) for item in value]
    return value


def _camel_details(details: Any) -> Any:
    """Domain details use snake_case (field paths like "structured_data.value", keys like
    "current_version"); the JSON API is camelCase throughout. Nested keys are converted too (e.g.
    each architecture violation's element_id); nested field paths name IR fields and are kept."""
    if not isinstance(details, dict):
        return details
    converted = {to_camel(key): _camel_keys(value) for key, value in details.items()}
    if isinstance(converted.get("field"), str):
        converted["field"] = ".".join(to_camel(part) for part in converted["field"].split("."))
    return converted


def _status_for(error: DomainError) -> int:
    for error_type in type(error).__mro__:
        if error_type in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[error_type]
    return 400


async def _domain_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, DomainError)  # noqa: S101 — registered for DomainError only
    status = _status_for(error)
    if status in _OBSERVED_STATUSES:
        security_log.warning(
            "request refused",
            extra={
                "code": error.code,
                "status": status,
                "method": request.method,
                "route": route_template(request.scope),
            },
        )
    return domain_error_response(error)


async def _validation_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)  # noqa: S101
    # Only location, message and type: never "input" or "ctx", which can contain secrets.
    fields = [
        {
            "field": ".".join(str(part) for part in issue["loc"]),
            "message": issue["msg"],
            "type": issue["type"],
        }
        for issue in error.errors()
    ]
    return error_response(422, "validation_error", "The request is invalid.", {"fields": fields})


async def _http_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)  # noqa: S101
    fallback = ("http_error", "The request could not be completed.")
    code, message = HTTP_CODES.get(error.status_code, fallback)
    return error_response(error.status_code, code, message)


async def _unexpected_error(request: Request, error: Exception) -> JSONResponse:
    logger.error(
        "unhandled error",
        exc_info=error,
        extra={"request_id": current_request_id(), "method": request.method, "path": request.url.path},
    )
    # Unhandled errors are answered by Starlette's outermost ServerErrorMiddleware, outside every
    # user middleware, so the security headers are added here directly.
    hsts = request.app.state.settings.environment == "production"
    return error_response(
        500,
        "internal_error",
        "Something went wrong on our side. Try again later.",
        headers=security_headers(docs=False, hsts=hsts),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)
