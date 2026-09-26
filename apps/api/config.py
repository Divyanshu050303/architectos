"""Service configuration, read from the environment (and a local .env file in development).

Security-sensitive values are never hard-coded here; they are declared with no default or
with a development-only default. ``environment=production`` turns on guards that refuse to
start with development-only choices (plain-text SMTP, an http frontend URL).
"""

from datetime import timedelta
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from apps.api.email.transport import SmtpSecurity


class Settings(BaseSettings):
    # env_ignore_empty: "KEY=" in a .env file means "use the default", as .env.example shows.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    environment: Literal["development", "test", "production"] = "development"

    database_url: PostgresDsn = Field(
        description="Async SQLAlchemy URL, e.g. postgresql+asyncpg://user:pass@host:5432/db",
    )
    database_echo: bool = False

    # Rate-limit counters. Unset: in-memory (single process only; refused in production).
    redis_url: RedisDsn | None = None
    rate_limit_enabled: bool = True

    # Interactive API docs at /api/docs. Defaults to off in production (see _defaults).
    api_docs_enabled: bool | None = None
    # Largest accepted request body; JSON payloads here are small.
    max_request_body_bytes: int = Field(default=64 * 1024, ge=1024)
    # Concurrent Argon2 hashes per process (each ~64 MiB).
    password_hash_concurrency: int = Field(default=4, ge=1, le=64)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # The web app: CORS origin and the base of every link in emails.
    frontend_url: AnyHttpUrl = AnyHttpUrl("http://localhost:3000")
    # Browser origins allowed to call the API (the web app). Comma-separated in the environment.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # Hosts avatar URLs may point to (comma-separated), e.g. your image CDN. Empty: any https host.
    # Recommended in production: third-party image URLs can track who views a profile.
    avatar_url_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # NIST SP 800-63B allows 8; ASVS 5 recommends 12. Upper bound is fixed by the policy (128).
    password_min_length: int = Field(default=12, ge=8, le=128)

    # Durations: ISO 8601 ("PT24H", "PT60S") or [HH:MM:]SS in the environment.
    email_verification_ttl: timedelta = Field(default=timedelta(hours=24), gt=timedelta(0))
    email_verification_resend_cooldown: timedelta = Field(default=timedelta(seconds=60), ge=timedelta(0))
    password_reset_ttl: timedelta = Field(default=timedelta(minutes=30), gt=timedelta(0))
    password_reset_cooldown: timedelta = Field(default=timedelta(seconds=60), ge=timedelta(0))
    invitation_ttl: timedelta = Field(default=timedelta(days=7), ge=timedelta(hours=1))

    # Signs access tokens (HS256). At least 32 characters; generate with
    # python -c "import secrets; print(secrets.token_urlsafe(48))". Required: no default.
    access_token_secret: SecretStr = Field(min_length=32)
    access_token_ttl: timedelta = Field(default=timedelta(minutes=15), gt=timedelta(0))
    # Absolute session lifetime from sign-in; refreshing does not extend it.
    refresh_token_ttl: timedelta = Field(default=timedelta(days=30), gt=timedelta(0))
    # A rotated-out refresh token presented within this window is a concurrent refresh
    # (another tab), not theft.
    refresh_reuse_grace: timedelta = Field(default=timedelta(seconds=10), ge=timedelta(0))

    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    # Unset: the cookie belongs to the API host only (recommended).
    cookie_domain: str | None = None

    email_from: str = "ArchitectOS <no-reply@localhost>"
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_security: SmtpSecurity = "none"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_timeout_seconds: float = Field(default=10, gt=0)

    # Requirements Engine: semantic extraction with a language model, off unless configured. The
    # deterministic engine never needs it; the model is only asked when the rules leave text unread.
    requirements_llm_provider: Literal["none", "anthropic"] = "none"
    anthropic_api_key: SecretStr | None = None
    requirements_llm_model: str = "claude-sonnet-5"
    requirements_llm_timeout_seconds: float = Field(default=20, gt=0, le=120)
    requirements_llm_max_output_tokens: int = Field(default=4000, ge=256, le=16_000)

    @field_validator("cors_allowed_origins", "avatar_url_allowed_hosts", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def docs_enabled(self) -> bool:
        return (
            self.api_docs_enabled if self.api_docs_enabled is not None else self.environment != "production"
        )

    @model_validator(mode="after")
    def _production_guards(self) -> Self:
        if self.requirements_llm_provider == "anthropic" and self.anthropic_api_key is None:
            raise ValueError("REQUIREMENTS_LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")
        if self.environment != "production":
            return self
        if not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        if self.rate_limit_enabled and self.redis_url is None:
            raise ValueError("REDIS_URL is required in production: in-memory rate limits are per process")
        if self.smtp_security == "none":
            raise ValueError("SMTP_SECURITY must be starttls or tls in production")
        if self.frontend_url.scheme != "https":
            raise ValueError("FRONTEND_URL must use https in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Settings are read once per process; tests build Settings directly instead."""
    return Settings()  # values come from the environment
