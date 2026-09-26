"""Builds the Requirements Engine from configuration: the deterministic engine, plus semantic
extraction only when a provider is configured. Kept apart from the engine so the engine itself never
knows which provider (if any) sits behind its port."""

from ai.agents.requirement_agent import RequirementExtractionAgent
from ai.llm.providers.anthropic import AnthropicStructuredLlm

from .service import RequirementsEngine


def build_engine(
    *,
    provider: str,
    api_key: str | None,
    model: str,
    timeout_seconds: float,
    max_output_tokens: int,
) -> RequirementsEngine:
    if provider == "anthropic" and api_key:
        llm = AnthropicStructuredLlm(api_key=api_key, model=model)
        agent = RequirementExtractionAgent(
            llm, max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds
        )
        return RequirementsEngine(agent)
    return RequirementsEngine()
