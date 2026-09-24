"""Response headers for a JSON API that browsers should never render, frame or cache.

The docs pages (/api/docs) load Swagger UI assets, so they get no Content-Security-Policy here;
every other response gets default-src 'none'. HSTS is sent only in production (over https).
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DOCS_PATHS = ("/api/docs", "/api/openapi.json")

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
}
API_ONLY = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    # Responses carry personal data; nothing is cacheable unless a route says otherwise.
    "Cache-Control": "no-store",
}
HSTS = "max-age=63072000; includeSubDomains"


def security_headers(*, docs: bool, hsts: bool) -> dict[str, str]:
    headers = BASE_HEADERS | ({} if docs else API_ONLY)
    return headers | {"Strict-Transport-Security": HSTS} if hsts else headers


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = str(scope.get("path", "")).startswith(DOCS_PATHS)

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in security_headers(docs=is_docs, hsts=self.hsts).items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, with_headers)
