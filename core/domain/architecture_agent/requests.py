"""What a person asks of the architecture agent, and the budget a run must stay within.

**The request** names a requirement set of the project — what the candidate is designed against and
traced to — and the person's objective in their own words. Constraints, preferences and exclusions
are kept as written and kept apart: a preference never becomes a constraint. An iteration names the
exact revision it starts from. **An omitted value is not a default**: no scale, availability, budget or
technology is assumed because it was left out — the gap is stated, or asked about.

**The budget** bounds every run: model calls (one proposal, at most one retry), tokens, wall-clock
time, retrieved passages and context size. Running out stops the run safely with ``budget_exhausted``
— never a partial candidate presented as complete.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from core.domain.text import has_forbidden_characters

from .errors import InvalidAgentRequest
from .values import check, count

MAX_OBJECTIVE = 4000
MAX_CONTEXT = 4000
MAX_ITEMS = 20
MAX_ITEM = 300
ALLOWED_CONTROLS = frozenset("\n\r\t")  # CRLF is normalized to LF


def _invalid(field_name: str, reason: str) -> InvalidAgentRequest:
    return InvalidAgentRequest(details={"field": field_name, "reason": reason})


def _clean(value: object, field_name: str, limit: int, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or has_forbidden_characters(value, ALLOWED_CONTROLS):
        raise _invalid(field_name, "invalid_text")
    cleaned = value.replace("\r\n", "\n").strip()
    if not cleaned:
        if required:
            raise _invalid(field_name, "required")
        return None
    if len(cleaned) > limit:
        raise _invalid(field_name, "too_long")
    return cleaned


def _list(values: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > MAX_ITEMS:
        raise _invalid(field_name, "too_many")
    cleaned = [_clean(v, field_name, MAX_ITEM, required=True) for v in values]
    return tuple(dict.fromkeys(v for v in cleaned if v))  # duplicates once, the order as written


@dataclass(frozen=True, slots=True)
class BaseRevision:
    """The revision an iteration starts from — exactly this one, never "the latest"."""

    architecture_id: uuid.UUID
    number: int

    def __post_init__(self) -> None:
        if not isinstance(self.architecture_id, uuid.UUID):
            raise _invalid("base.architecture_id", "required")
        if count(self.number, "base.number", minimum=1):
            raise _invalid("base.number", "invalid_revision")


@dataclass(frozen=True, slots=True)
class AgentRequest:
    requirement_set_id: uuid.UUID
    objective: str  # the person's own words: what the system should do
    constraints: tuple[str, ...] = ()  # hard: "must run on AWS", "no Kafka"
    preferences: tuple[str, ...] = ()  # soft: "prefer managed services"
    exclusions: tuple[str, ...] = ()  # out of scope: "no mobile client"
    context: str | None = None  # anything else the person wants considered
    base: BaseRevision | None = None  # an iteration on this revision

    def __post_init__(self) -> None:
        if not isinstance(self.requirement_set_id, uuid.UUID):
            raise _invalid("requirement_set_id", "required")
        object.__setattr__(
            self, "objective", _clean(self.objective, "objective", MAX_OBJECTIVE, required=True)
        )
        object.__setattr__(self, "context", _clean(self.context, "context", MAX_CONTEXT, required=False))
        for name in ("constraints", "preferences", "exclusions"):
            object.__setattr__(self, name, _list(getattr(self, name), name))
        if self.base is not None and not isinstance(self.base, BaseRevision):
            raise _invalid("base", "invalid")

    def to_dict(self) -> dict[str, Any]:
        base = self.base
        return {
            "requirement_set_id": str(self.requirement_set_id),
            "objective": self.objective,
            "constraints": list(self.constraints),
            "preferences": list(self.preferences),
            "exclusions": list(self.exclusions),
            "context": self.context,
            "base": {"architecture_id": str(base.architecture_id), "number": base.number} if base else None,
        }


@dataclass(frozen=True, slots=True)
class Budget:
    """The limits of one run. Defaults are conservative; deployments may lower them, never remove them."""

    max_model_calls: int = 2  # the proposal, and at most one retry of a retryable failure
    max_input_tokens: int = 60_000  # summed over model calls
    max_output_tokens: int = 8_000  # per model call
    max_seconds: float = 90.0  # wall-clock, the whole run
    max_passages: int = 20  # retrieved knowledge passages in the context
    max_context_chars: int = 60_000  # the assembled context, as sent

    def __post_init__(self) -> None:
        seconds = self.max_seconds
        check(
            [
                count(self.max_model_calls, "budget.max_model_calls", minimum=1),
                count(self.max_input_tokens, "budget.max_input_tokens", minimum=1000),
                count(self.max_output_tokens, "budget.max_output_tokens", minimum=256),
                None if isinstance(seconds, int | float) and 0 < seconds <= 600 else "budget.max_seconds",
                count(self.max_passages, "budget.max_passages"),
                count(self.max_context_chars, "budget.max_context_chars", minimum=1000),
            ]
        )
        if self.max_model_calls > 3:
            raise _invalid("budget.max_model_calls", "too_many")  # no open-ended loops

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_seconds": self.max_seconds,
            "max_passages": self.max_passages,
            "max_context_chars": self.max_context_chars,
        }


def _add(total: int | None, more: int | None) -> int | None:
    return None if total is None or more is None else total + more  # unknown stays unknown


@dataclass(frozen=True, slots=True)
class AgentUsage:
    """What a run used. Tokens as the provider reported them (``None``: not reported — never 0).
    No price: provider pricing is not configured, so cost is unavailable, not zero."""

    model_calls: int = 0
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    model_latency_ms: int = 0
    retrieval_calls: int = 0
    engine_runs: int = 0

    def __post_init__(self) -> None:
        check(
            [
                count(self.model_calls, "usage.model_calls"),
                count(self.input_tokens, "usage.input_tokens", required=False),
                count(self.output_tokens, "usage.output_tokens", required=False),
                count(self.model_latency_ms, "usage.model_latency_ms"),
                count(self.retrieval_calls, "usage.retrieval_calls"),
                count(self.engine_runs, "usage.engine_runs"),
            ]
        )

    def with_model_call(
        self, input_tokens: int | None, output_tokens: int | None, latency_ms: int
    ) -> AgentUsage:
        return AgentUsage(
            self.model_calls + 1,
            _add(self.input_tokens, input_tokens),
            _add(self.output_tokens, output_tokens),
            self.model_latency_ms + latency_ms,
            self.retrieval_calls,
            self.engine_runs,
        )

    def exceeded(self, budget: Budget, elapsed_seconds: float) -> str | None:
        """The first limit exceeded, or None."""
        if self.model_calls > budget.max_model_calls:
            return "model_calls"
        if self.input_tokens is not None and self.input_tokens > budget.max_input_tokens:
            return "input_tokens"
        if elapsed_seconds > budget.max_seconds:
            return "seconds"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model_latency_ms": self.model_latency_ms,
            "retrieval_calls": self.retrieval_calls,
            "engine_runs": self.engine_runs,
            "cost": None,  # pricing is not configured: unavailable, not zero
        }
