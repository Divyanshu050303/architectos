"""Service configuration, read from the environment (and a local .env file in development).

Security-sensitive values are never hard-coded here; they are declared with no default or
with a development-only default that production deployments must override.
"""

from functools import lru_cache

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: PostgresDsn = Field(
        description="Async SQLAlchemy URL, e.g. postgresql+asyncpg://user:pass@host:5432/db",
    )
    database_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    """Settings are read once per process; tests clear the cache to override them."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
