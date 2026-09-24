"""ASGI application factory. Run with: uvicorn --factory apps.api.main:create_app"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.config import Settings, get_settings
from apps.api.email.transport import SmtpTransport
from apps.api.exception_handlers import register_exception_handlers
from apps.api.lifespan import lifespan
from apps.api.middleware.request_id import HEADER as REQUEST_ID_HEADER
from apps.api.middleware.request_id import RequestIdMiddleware
from apps.api.routes import auth

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="ArchitectOS API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    settings = settings or get_settings()
    app.state.settings = settings
    app.state.email_transport = SmtpTransport(
        host=settings.smtp_host,
        port=settings.smtp_port,
        security=settings.smtp_security,
        username=settings.smtp_username,
        password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
        timeout_seconds=settings.smtp_timeout_seconds,
    )

    register_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    # Added last, so it is the outermost middleware and every response carries the id.
    app.add_middleware(RequestIdMiddleware)

    app.include_router(auth.router, prefix=API_PREFIX)
    return app
