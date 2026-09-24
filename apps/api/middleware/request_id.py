"""Gives every request an id: the caller's X-Request-ID if well-formed, otherwise a new one.

The id is echoed in the X-Request-ID response header, included in every error body and
available to log records through ``current_request_id()``.
"""

import re
import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "X-Request-ID"
# Bounded charset and length: the value ends up in logs and response headers.
_VALID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    return _request_id.get()


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(HEADER.lower().encode(), b"").decode("latin-1")
        request_id = incoming if _VALID.fullmatch(incoming) else f"req_{uuid.uuid4().hex}"
        _request_id.set(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)
