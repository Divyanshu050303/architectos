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
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=message):
        Settings(database_url=DB, access_token_secret=SECRET, _env_file=None)


def test_production_accepts_tls_and_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
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
