"""The architecture agent in API tests: the real pipeline (context, builder, catalog, engines) with a
scripted model in place of a provider — nothing leaves the process."""

from typing import Any

from fastapi import FastAPI

from ai.agents.architecture_agent import ArchitectureProposalAgent
from engines.architecture_agent.orchestrator import AgentEngines, ArchitectureAgentPipeline
from tests.unit.ai.fakes import ScriptedLlm

# A small valid proposal: an API and its database. It cites no requirement and no passage, so it fits
# any requirement set and any project's knowledge.
AGENT_OUTPUT: dict[str, Any] = {
    "name": "Orders",
    "summary": "An API in front of a relational database.",
    "confidence": 0.7,
    "nodes": [
        {
            "id": "orders-api",
            "kind": "service",
            "name": "Orders API",
            "rationale": "Serves orders",
            "confidence": 0.9,
        },
        {
            "id": "orders-db",
            "kind": "database",
            "name": "Orders DB",
            "rationale": "Stores orders",
            "component": "databases/postgresql",
            "confidence": 0.8,
        },
    ],
    "connections": [
        {
            "id": "api-db",
            "source": "orders-api",
            "target": "orders-db",
            "kind": "data_access",
            "rationale": "Reads and writes orders",
            "protocol": "postgresql",
            "confidence": 0.9,
        }
    ],
    "claims": [{"statement": "Peak traffic is not stated", "basis": "unknown", "confidence": 1}],
    "questions": ["What is the peak order rate?"],
}


def scripted_pipeline(app: FastAPI, llm: ScriptedLlm | None = None) -> ArchitectureAgentPipeline:
    state = app.state
    engines = AgentEngines(
        state.validation_engine, state.reliability_engine, state.security_engine, state.observability_engine
    )
    proposer = ArchitectureProposalAgent(llm or ScriptedLlm(AGENT_OUTPUT))
    return ArchitectureAgentPipeline(proposer, engines, state.component_catalog)
