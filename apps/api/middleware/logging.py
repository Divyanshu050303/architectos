"""One structured log line per request: method, route template, status, duration, request id.

The route template (/api/v1/invitations/{invitation_token}/accept) is logged, never the raw path,
so tokens and ids in URLs stay out of logs. No IPs, user agents, headers or bodies are logged here.
"""

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("architectos.request")


def route_template(scope: Scope) -> str:
    """The matched route with its parameters named, e.g. /api/v1/invitations/{invitation_token}/accept.

    Built from the request path by replacing each path-parameter segment with {name}, so the result
    includes router prefixes and never contains a parameter's value."""
    if scope.get("route") is None:
        return "<unmatched>"
    by_value = {str(value): name for name, value in (scope.get("path_params") or {}).items()}
    segments = str(scope.get("path", "")).split("/")
    return "/".join(f"{{{by_value[s]}}}" if s in by_value else s for s in segments)


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "route": route_template(scope),
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
