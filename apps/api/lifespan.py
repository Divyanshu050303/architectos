from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.config import Settings
from apps.api.logging_config import configure_logging
from apps.api.middleware.rate_limit import RateLimiter
from persistence.database import create_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    engine = create_engine(str(settings.database_url), echo=settings.database_echo)
    app.state.session_factory = create_session_factory(engine)
    try:
        yield
    finally:
        limiter: RateLimiter = app.state.rate_limiter
        await limiter.close()
        await engine.dispose()
