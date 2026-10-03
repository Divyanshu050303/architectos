"""A workflow's limits and what it has used. Autonomy is bounded: every expensive action is checked
against what is left *before* it runs, and a workflow that reaches a limit stops — with a reviewable
package if it has one, failed if it has none. It never continues past a limit.

Defaults are conservative; a deployment may lower them (``ARCHITECTURE_WORKFLOW_*``), and a request
may lower them further, never raise them above the ceilings here.
"""

from dataclasses import dataclass, fields, replace
from typing import Any

from .errors import InvalidWorkflowRequest
from .values import Action, check, count

# The ceilings: no configuration or request can go beyond them.
CEILINGS: dict[str, int | float] = {
    "max_iterations": 5,
    "max_llm_calls": 20,
    "max_tool_calls": 60,
    "max_retrievals": 6,
    "max_candidates": 8,
    "max_simulations": 6,
    "max_input_tokens": 400_000,
    "max_seconds": 3600.0,
}
LLM_ACTIONS = frozenset(
    {
        Action.ANALYZE_REQUIREMENTS,
        Action.GENERATE_ARCHITECTURE,
        Action.GENERATE_ALTERNATIVE,
        Action.COMPARE_CANDIDATES,
    }
)  # may call a model (at most 2 calls each: a call and one retry)
CALLS_PER_LLM_ACTION = 2
MAY_BE_ZERO = frozenset({"max_iterations", "max_retrievals", "max_simulations"})


@dataclass(frozen=True, slots=True)
class WorkflowBudget:
    max_iterations: int = 3  # improvement rounds after the first candidate
    max_llm_calls: int = 12
    max_tool_calls: int = 40  # actions executed, of any kind
    max_retrievals: int = 4
    max_candidates: int = 6
    max_simulations: int = 3
    max_input_tokens: int = 240_000  # summed over model calls
    max_seconds: float = 1800.0  # wall-clock, from start to review

    def __post_init__(self) -> None:
        problems: list[str | None] = []
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "max_seconds":
                number = isinstance(value, int | float) and not isinstance(value, bool)
                problems.append(None if number and value >= 60 else f"budget.{f.name}")
            else:
                minimum = 0 if f.name in MAY_BE_ZERO else 1
                problems.append(count(value, f"budget.{f.name}", minimum=minimum))
        check(problems)
        for f in fields(self):
            if getattr(self, f.name) > CEILINGS[f.name]:
                raise InvalidWorkflowRequest(details={"field": f.name, "reason": "above_ceiling"})

    def lowered(self, **limits: Any) -> WorkflowBudget:
        """These limits, lowered by a request; raising one is refused."""
        for name, value in limits.items():
            if value > getattr(self, name):
                raise InvalidWorkflowRequest(details={"field": name, "reason": "above_configured"})
        return replace(self, **limits)

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True, slots=True)
class WorkflowUsage:
    """What the workflow used. Token counts a provider did not report are unknown (``None``), never 0."""

    iterations: int = 0
    llm_calls: int = 0
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    tool_calls: int = 0
    retrievals: int = 0
    candidates: int = 0
    simulations: int = 0

    def __post_init__(self) -> None:
        counted = ("iterations", "llm_calls", "tool_calls", "retrievals", "candidates", "simulations")
        check(
            [
                *(count(getattr(self, n), f"usage.{n}") for n in counted),
                count(self.input_tokens, "usage.input_tokens", required=False),
                count(self.output_tokens, "usage.output_tokens", required=False),
            ]
        )

    def plus(self, other: WorkflowUsage) -> WorkflowUsage:
        def add(a: int | None, b: int | None) -> int | None:
            return None if a is None or b is None else a + b

        return WorkflowUsage(
            self.iterations + other.iterations,
            self.llm_calls + other.llm_calls,
            add(self.input_tokens, other.input_tokens),
            add(self.output_tokens, other.output_tokens),
            self.tool_calls + other.tool_calls,
            self.retrievals + other.retrievals,
            self.candidates + other.candidates,
            self.simulations + other.simulations,
        )

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def blocking_limit(
    budget: WorkflowBudget, usage: WorkflowUsage, action: Action, elapsed_seconds: float
) -> str | None:
    """The limit that forbids ``action`` now, or None. Checked before the action runs: an action that
    may call a model needs room for its call and one retry."""
    found: str | None = None
    if elapsed_seconds >= budget.max_seconds:
        found = "max_seconds"
    elif usage.tool_calls >= budget.max_tool_calls:
        found = "max_tool_calls"
    elif action in LLM_ACTIONS and usage.llm_calls + CALLS_PER_LLM_ACTION > budget.max_llm_calls:
        found = "max_llm_calls"
    elif (
        action in LLM_ACTIONS
        and usage.input_tokens is not None
        and usage.input_tokens >= budget.max_input_tokens
    ):
        found = "max_input_tokens"
    elif action in {Action.GENERATE_ARCHITECTURE, Action.GENERATE_ALTERNATIVE} and (
        usage.candidates >= budget.max_candidates
    ):
        found = "max_candidates"
    elif action is Action.GENERATE_ALTERNATIVE and usage.iterations >= budget.max_iterations:
        found = "max_iterations"
    elif action is Action.RETRIEVE_KNOWLEDGE and usage.retrievals >= budget.max_retrievals:
        found = "max_retrievals"
    elif action is Action.RUN_SIMULATION and usage.simulations >= budget.max_simulations:
        found = "max_simulations"
    return found
