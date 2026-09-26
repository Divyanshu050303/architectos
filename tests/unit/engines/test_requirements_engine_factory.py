import pytest
from pydantic import ValidationError

from apps.api.config import Settings
from engines.requirements.factory import build_engine

BASE = {"database_url": "postgresql+asyncpg://u:p@localhost/db", "access_token_secret": "x" * 32}


def test_the_model_is_off_unless_configured() -> None:
    assert Settings(**BASE).requirements_llm_provider == "none"  # type: ignore[arg-type]
    engine = build_engine(provider="none", api_key=None, model="m", timeout_seconds=5, max_output_tokens=500)
    assert engine.semantic_source is None


def test_anthropic_needs_a_key() -> None:
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        Settings(**BASE, requirements_llm_provider="anthropic")  # type: ignore[arg-type]
    settings = Settings(**BASE, requirements_llm_provider="anthropic", anthropic_api_key="sk-test")  # type: ignore[arg-type]
    assert "sk-test" not in repr(settings)  # a secret, never printed


def test_a_configured_provider_is_wired_behind_the_port() -> None:
    engine = build_engine(
        provider="anthropic",
        api_key="sk-test",
        model="claude-sonnet-5",
        timeout_seconds=5,
        max_output_tokens=500,
    )
    assert engine.semantic_source == "anthropic/claude-sonnet-5"
