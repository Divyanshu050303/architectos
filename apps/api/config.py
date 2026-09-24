"""Service configuration, read from the environment (and a local .env file in development).

Security-sensitive values are never hard-coded here; they are declared with no default or
with a development-only default that production deployments must override.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: PostgresDsn = Field(
        description="Async SQLAlchemy URL, e.g. postgresql+asyncpg://user:pass@host:5432/db",
    )
    database_echo: bool = False

    # Browser origins allowed to call the API (the web app). Comma-separated in the environment.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # NIST SP 800-63B allows 8; ASVS 5 recommends 12. Upper bound is fixed by the policy (128).
    password_min_length: int = Field(default=12, ge=8, le=128)

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Settings are read once per process; tests build Settings directly instead."""
    return Settings()  # values come from the environment
