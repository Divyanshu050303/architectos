"""Refuses request bodies larger than MAX_REQUEST_BODY_BYTES before the app reads them.

A declared Content-Length over the limit is answered with 413 immediately. A body without a
declared length (chunked) is counted while it is read and cut off once over the limit.

A few routes legitimately take larger bodies (a whole architecture document); each is listed with
its own limit, matched on method and path, and every other route keeps the default.
"""

import json
import re
from collections.abc import Sequence

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.api.middleware.request_id import HEADER as REQUEST_ID_HEADER
from apps.api.middleware.request_id import current_request_id


class BodyTooLarge(Exception):
    pass


async def _reject(send: Send) -> None:
    request_id = current_request_id()
    body = json.dumps(
        {
            "error": {
                "code": "payload_too_large",
                "message": "The request body is too large.",
                "details": None,
                "request_id": request_id,
            }
        }
    ).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if request_id:
        headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
    await send({"type": "http.response.start", "status": 413, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class BodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        larger: Sequence[tuple[str, re.Pattern[str], int]] = (),
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.larger = tuple(larger)  # (method, full-path pattern, limit)

    def _limit(self, scope: Scope) -> int:
        for method, path, limit in self.larger:
            if scope.get("method") == method and path.fullmatch(scope.get("path", "")):
                return limit
        return self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        max_bytes = self._limit(scope)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > max_bytes
            except ValueError:
                too_large = True
            if too_large:
                await _reject(send)
                return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise BodyTooLarge
            return message

        try:
            await self.app(scope, counting_receive, send)
        except BodyTooLarge:
            await _reject(send)
