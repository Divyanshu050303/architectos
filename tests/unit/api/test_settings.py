import pytest

from apps.api.config import Settings

DB = "postgresql+asyncpg://user:pass@localhost:5432/db"


def test_cors_origins_accept_a_comma_separated_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000, https://app.example.com")
    settings = Settings(database_url=DB, _env_file=None)
    assert settings.cors_allowed_origins == ["http://localhost:3000", "https://app.example.com"]


@pytest.mark.parametrize("value", ["7", "129"])
def test_password_minimum_is_bounded(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", value)
    with pytest.raises(ValueError, match="password_min_length"):
        Settings(database_url=DB, _env_file=None)
