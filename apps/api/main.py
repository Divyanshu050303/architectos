"""ASGI application factory. Run with: uvicorn --factory apps.api.main:create_app --no-access-log"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.config import Settings, get_settings
from apps.api.dependencies.auth import CSRF_HEADER
from apps.api.email.transport import SmtpTransport
from apps.api.exception_handlers import register_exception_handlers
from apps.api.lifespan import lifespan
from apps.api.middleware.body_limit import BodyLimitMiddleware
from apps.api.middleware.logging import RequestLoggingMiddleware
from apps.api.middleware.rate_limit import InMemoryRateLimiter, RateLimiter, RedisRateLimiter
from apps.api.middleware.request_id import HEADER as REQUEST_ID_HEADER
from apps.api.middleware.request_id import RequestIdMiddleware
from apps.api.middleware.security_headers import SecurityHeadersMiddleware
from apps.api.routes import auth, invitations, organizations, projects, requirements, users

API_PREFIX = "/api/v1"


def _rate_limiter(settings: Settings) -> RateLimiter:
    if settings.redis_url is not None:
        return RedisRateLimiter(str(settings.redis_url))
    return InMemoryRateLimiter()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="ArchitectOS API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.state.rate_limiter = _rate_limiter(settings)
    app.state.email_transport = SmtpTransport(
        host=settings.smtp_host,
        port=settings.smtp_port,
        security=settings.smtp_security,
        username=settings.smtp_username,
        password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
        timeout_seconds=settings.smtp_timeout_seconds,
    )

    register_exception_handlers(app)
    # Middleware runs outermost-last-added. Resulting order for a request:
    # RequestId -> RequestLogging -> SecurityHeaders -> BodyLimit -> CORS -> routes.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", REQUEST_ID_HEADER, CSRF_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
        # Credentials (the refresh cookie) are only accepted from the allow-listed origins above.
        allow_credentials=True,
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.environment == "production")
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(users.router, prefix=API_PREFIX)
    app.include_router(organizations.router, prefix=API_PREFIX)
    app.include_router(invitations.router, prefix=API_PREFIX)
    app.include_router(projects.router, prefix=API_PREFIX)
    app.include_router(requirements.router, prefix=API_PREFIX)
    return app
