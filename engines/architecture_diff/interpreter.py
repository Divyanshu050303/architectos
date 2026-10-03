"""The AI interpretation of a stored diff, in a fixed order: project knowledge (at most two bounded
queries, through the caller's retriever), the bounded context, then one explainer call (at most one
retry, inside the explainer).

- Identical states are ``not_needed``: nothing is retrieved and the model is not called.
- A retrieval failure is said, and the explanation proceeds without passages.
- A diff too large for the context budget fails ``budget_exhausted`` without calling the model.
- Without a configured model every explanation fails ``llm_unavailable``, with nothing sent anywhere.

The run keeps the passages the explanation cites (their citations, never their text).
"""

import uuid
from datetime import datetime
from typing import Any

from ai.agents.diff_agent import PROMPT_VERSION, DiffExplanationAgent
from ai.llm.providers.anthropic import AnthropicStructuredLlm
from core.domain.architecture_agent.requests import AgentUsage
from core.domain.architecture_diff.diffs import ArchitectureDiff
from core.domain.architecture_diff.explanations import ExplanationRun
from core.domain.architecture_diff.ports import (
    DiffExplainer,
    ExplainOutcome,
    ExplanationBudget,
    ExplanationContext,
    Retrieve,
)
from core.domain.architecture_diff.values import Basis, ExplanationFailure, ExplanationStatus
from core.domain.knowledge.retrieval import RetrievalResult
from engines.architecture_agent.context import evidence_ref

from .explanation_context import assemble, merged, retrieval_queries

NOT_CONFIGURED = "none/not-configured"
NOTHING_TO_EXPLAIN = "The two states are identical: there is nothing to explain."
MAX_LIMITATIONS = 50


class NotConfigured:
    """The explainer when no language model is configured: it calls nothing."""

    @property
    def model(self) -> str:
        return NOT_CONFIGURED

    async def explain(self, context: ExplanationContext, budget: ExplanationBudget) -> ExplainOutcome:
        return ExplainOutcome(
            NOT_CONFIGURED,
            PROMPT_VERSION,
            AgentUsage(),
            failure=ExplanationFailure.LLM_UNAVAILABLE,
            attempts=("not_configured",),
        )


class DiffInterpretation:
    """Implements ``DiffInterpreter`` with any ``DiffExplainer``."""

    def __init__(self, explainer: DiffExplainer) -> None:
        self._explainer = explainer

    async def interpret(
        self,
        diff: ArchitectureDiff,
        retrieve: Retrieve,
        budget: ExplanationBudget,
        *,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        now: datetime,
    ) -> ExplanationRun:
        def run(status: ExplanationStatus, **fields: Any) -> ExplanationRun:
            return ExplanationRun(run_id, diff.id, status, user_id, now, **fields)

        if diff.identical:
            return run(ExplanationStatus.NOT_NEEDED, limitations=(NOTHING_TO_EXPLAIN,))
        queries = retrieval_queries(diff, budget)
        results: list[RetrievalResult] = []
        limitations: list[str] = []
        for query in queries:
            try:
                results.append(await retrieve(query))
            except Exception as error:  # no knowledge, or not allowed to read it: said, and proceed
                code = getattr(error, "code", type(error).__name__)
                limitations.append(f"Project knowledge could not be retrieved ({code}); no passage was used.")
                results = []
                break
        for result in results:
            limitations += result.limitations
        assembled = assemble(diff, merged((r.passages for r in results), budget), budget)
        limitations += assembled.limitations
        said = tuple(dict.fromkeys(limitations))[:MAX_LIMITATIONS]
        spent = AgentUsage(retrieval_calls=len(queries))
        if assembled.context is None:
            failure = ExplanationFailure.BUDGET_EXHAUSTED
            return run(ExplanationStatus.FAILED, usage=spent, failure=failure, limitations=said)
        outcome = await self._explainer.explain(assembled.context, budget)
        common: dict[str, Any] = {
            "model": outcome.model,
            "prompt_version": outcome.prompt_version,
            "usage": spent.plus(outcome.usage),
            "raw_output": outcome.raw,
            "limitations": said,
        }
        if outcome.explanation is None:
            refused = outcome.rejections
            return run(ExplanationStatus.FAILED, failure=outcome.failure, rejections=refused, **common)
        cited = {r for s in outcome.explanation.statements() for r in s.refs(Basis.EVIDENCE)}
        evidence = tuple(evidence_ref(p) for p in assembled.evidence if p.citation.chunk_id in cited)
        return run(ExplanationStatus.COMPLETED, explanation=outcome.explanation, evidence=evidence, **common)


def build_interpreter(
    *, provider: str, api_key: str | None, model: str, timeout_seconds: float
) -> DiffInterpretation:
    if provider == "anthropic" and api_key:
        llm = AnthropicStructuredLlm(api_key=api_key, model=model)
        return DiffInterpretation(DiffExplanationAgent(llm, timeout_seconds=timeout_seconds))
    return DiffInterpretation(NotConfigured())
