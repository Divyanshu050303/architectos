"""Refuses request bodies larger than MAX_REQUEST_BODY_BYTES before the app reads them.

A declared Content-Length over the limit is answered with 413 immediately. A body without a
declared length (chunked) is counted while it is read and cut off once over the limit.
"""

import json

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
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > self.max_bytes
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
                if received > self.max_bytes:
                    raise BodyTooLarge
            return message

        try:
            await self.app(scope, counting_receive, send)
        except BodyTooLarge:
            await _reject(send)
