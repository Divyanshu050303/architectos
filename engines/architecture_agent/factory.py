"""Builds the architecture agent's pipeline from configuration. Kept apart from the pipeline so it never
knows which provider (if any) sits behind the proposer port.

Without a configured provider the proposer is ``NotConfigured``: every run reaching the proposal stage
fails ``llm_unavailable``, with nothing sent anywhere — never a placeholder design."""

from ai.agents.architecture_agent import PROMPT_VERSION, ArchitectureProposalAgent
from ai.llm.providers.anthropic import AnthropicStructuredLlm
from core.domain.architecture_agent.ports import ProposalContext, ProposerOutcome
from core.domain.architecture_agent.requests import AgentUsage, Budget
from core.domain.architecture_agent.values import FailureCode
from core.domain.components.repository import ComponentCatalog

from .orchestrator import AgentEngines, ArchitectureAgentPipeline

NOT_CONFIGURED = "none/not-configured"


class NotConfigured:
    """The proposer when no language model is configured: it calls nothing."""

    configured = False

    @property
    def model(self) -> str:
        return NOT_CONFIGURED

    async def propose(
        self, context: ProposalContext, budget: Budget, *, spent: AgentUsage, remaining_seconds: float
    ) -> ProposerOutcome:
        failure = FailureCode.LLM_UNAVAILABLE
        return ProposerOutcome(
            NOT_CONFIGURED, PROMPT_VERSION, AgentUsage(), failure=failure, attempts=("not_configured",)
        )


def build_pipeline(
    *,
    provider: str,
    api_key: str | None,
    model: str,
    timeout_seconds: float,
    engines: AgentEngines,
    catalog: ComponentCatalog,
) -> ArchitectureAgentPipeline:
    if provider == "anthropic" and api_key:
        llm = AnthropicStructuredLlm(api_key=api_key, model=model)
        proposer = ArchitectureProposalAgent(llm, timeout_seconds=timeout_seconds)
        return ArchitectureAgentPipeline(proposer, engines, catalog)
    return ArchitectureAgentPipeline(NotConfigured(), engines, catalog)
