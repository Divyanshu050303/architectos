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
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.middleware.request_id import HEADER as REQUEST_ID_HEADER
from apps.api.middleware.request_id import current_request_id
from core.domain.errors import DomainError
from core.domain.identity.errors import InvalidEmail, InvalidName, WeakPassword

logger = logging.getLogger("architectos.api")

# Domain error -> HTTP status. Errors not listed are 400.
STATUS_BY_ERROR: dict[type[DomainError], int] = {
    InvalidEmail: 422,
    InvalidName: 422,
    WeakPassword: 422,
}

HTTP_CODES: dict[int, tuple[str, str]] = {
    404: ("not_found", "Not found."),
    405: ("method_not_allowed", "This method is not allowed here."),
}


def error_response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    request_id = current_request_id()
    body = {"error": {"code": code, "message": message, "details": details, "request_id": request_id}}
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(status_code=status, content=body, headers=headers)


def _status_for(error: DomainError) -> int:
    for error_type in type(error).__mro__:
        if error_type in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[error_type]
    return 400


async def _domain_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, DomainError)  # noqa: S101 — registered for DomainError only
    return error_response(_status_for(error), error.code, error.detail_message, error.details)


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
    return error_response(500, "internal_error", "Something went wrong on our side. Try again later.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)
