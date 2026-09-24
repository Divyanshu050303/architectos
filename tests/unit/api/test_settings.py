import pytest

from apps.api.config import Settings

DB = "postgresql+asyncpg://user:pass@localhost:5432/db"
SECRET = "s" * 40


def test_cors_origins_accept_a_comma_separated_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000, https://app.example.com")
    settings = Settings(database_url=DB, access_token_secret=SECRET, _env_file=None)
    assert settings.cors_allowed_origins == ["http://localhost:3000", "https://app.example.com"]


@pytest.mark.parametrize("value", ["7", "129"])
def test_password_minimum_is_bounded(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", value)
    with pytest.raises(ValueError, match="password_min_length"):
        Settings(database_url=DB, access_token_secret=SECRET, _env_file=None)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"SMTP_SECURITY": "none", "FRONTEND_URL": "https://app.example.com"}, "SMTP_SECURITY"),
        ({"SMTP_SECURITY": "tls", "FRONTEND_URL": "http://app.example.com"}, "FRONTEND_URL"),
    ],
)
def test_production_refuses_development_only_choices(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str], message: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REDIS_URL", "redis://redis.internal:6379/0")
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=message):
        Settings(database_url=DB, access_token_secret=SECRET, _env_file=None)


def test_production_accepts_tls_and_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REDIS_URL", "redis://redis.internal:6379/0")
    monkeypatch.setenv("SMTP_SECURITY", "starttls")
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
    assert Settings(database_url=DB, access_token_secret=SECRET, _env_file=None).environment == "production"


def test_durations_accept_iso_8601(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_VERIFICATION_TTL", "PT2H")
    monkeypatch.setenv("EMAIL_VERIFICATION_RESEND_COOLDOWN", "PT30S")
    settings = Settings(database_url=DB, access_token_secret=SECRET, _env_file=None)
    assert settings.email_verification_ttl.total_seconds() == 7200
    assert settings.email_verification_resend_cooldown.total_seconds() == 30


def test_access_token_secret_is_required_and_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACCESS_TOKEN_SECRET", raising=False)
    with pytest.raises(ValueError, match="access_token_secret"):
        Settings(database_url=DB, _env_file=None)
    with pytest.raises(ValueError, match="access_token_secret"):
        Settings(database_url=DB, access_token_secret="short", _env_file=None)


def test_secret_is_never_shown_in_settings_repr() -> None:
    settings = Settings(database_url=DB, access_token_secret=SECRET, _env_file=None)
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings.model_dump())


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"COOKIE_SAMESITE": "none", "COOKIE_SECURE": "false"}, "COOKIE_SAMESITE"),
        (
            {
                "ENVIRONMENT": "production",
                "COOKIE_SECURE": "false",
                "SMTP_SECURITY": "tls",
                "FRONTEND_URL": "https://app.example.com",
            },
            "COOKIE_SECURE",
        ),
    ],
)
def test_cookie_guards(monkeypatch: pytest.MonkeyPatch, env: dict[str, str], message: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=message):
        Settings(database_url=DB, access_token_secret=SECRET, _env_file=None)


def test_production_requires_shared_rate_limit_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "ENVIRONMENT": "production",
        "SMTP_SECURITY": "tls",
        "FRONTEND_URL": "https://app.example.com",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(database_url=DB, access_token_secret=SECRET, _env_file=None)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")  # an explicit opt-out is allowed
    assert Settings(database_url=DB, access_token_secret=SECRET, _env_file=None).redis_url is None


def test_docs_default_off_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SMTP_SECURITY", "tls")
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
    monkeypatch.setenv("REDIS_URL", "redis://redis.internal:6379/0")
    assert Settings(database_url=DB, access_token_secret=SECRET, _env_file=None).docs_enabled is False
    monkeypatch.setenv("API_DOCS_ENABLED", "true")
    assert Settings(database_url=DB, access_token_secret=SECRET, _env_file=None).docs_enabled is True
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("API_DOCS_ENABLED")
    assert Settings(database_url=DB, access_token_secret=SECRET, _env_file=None).docs_enabled is True


def test_empty_values_mean_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "API_DOCS_ENABLED",
        "COOKIE_DOMAIN",
        "SMTP_USERNAME",
        "REDIS_URL",
        "AVATAR_URL_ALLOWED_HOSTS",
    ):
        monkeypatch.setenv(key, "")
    settings = Settings(database_url=DB, access_token_secret=SECRET, _env_file=None)
    assert settings.api_docs_enabled is None
    assert (settings.cookie_domain, settings.smtp_username, settings.redis_url) == (None, None, None)


def test_the_example_env_file_loads(tmp_path: object) -> None:
    import pathlib  # noqa: PLC0415

    example = pathlib.Path(".env.example").read_text()
    example = example.replace("ACCESS_TOKEN_SECRET=\n", f"ACCESS_TOKEN_SECRET={SECRET}\n")
    env_file = pathlib.Path(str(tmp_path)) / ".env"
    env_file.write_text(example)
    settings = Settings(_env_file=env_file)
    assert settings.redis_url is not None
    assert settings.docs_enabled
