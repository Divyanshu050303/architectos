"""The provider-agnostic port to language models, for schema-constrained output only.

Engines never talk to a provider: they send a ``StructuredRequest`` (system instructions, the
untrusted user content kept separate, a JSON schema, limits) and get a ``StructuredResponse`` (the
parsed JSON and the usage) or one of the ``LlmError``s. Which provider answers (Anthropic, a local
model, a test double) is configuration.

The output is *proposed data*. Callers validate every field deterministically before using it.
"""

from dataclasses import dataclass
from typing import Any, Protocol


class LlmError(Exception):
    """A model could not produce a usable answer. ``code`` is stable and safe to record."""

    code = "llm_error"


class LlmUnavailable(LlmError):
    code = "llm_unavailable"  # not configured, unreachable, rate-limited, overloaded, refused


class LlmTimeout(LlmError):
    code = "llm_timeout"


class LlmMalformedOutput(LlmError):
    code = "llm_malformed_output"  # not JSON, or not the requested shape


@dataclass(frozen=True, slots=True)
class StructuredRequest:
    system: str  # instructions: never contains user content
    user_content: str  # untrusted data, delimited by the caller's prompt
    schema: dict[str, Any]  # JSON schema the output must follow
    max_output_tokens: int
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class Usage:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class StructuredResponse:
    data: Any  # parsed JSON; not yet validated against anything but the provider's schema support
    usage: Usage


class StructuredLlm(Protocol):
    @property
    def name(self) -> str:
        """``provider/model``, recorded with every analysis that used it."""
        ...

    async def complete(self, request: StructuredRequest) -> StructuredResponse:
        """Raises ``LlmUnavailable``, ``LlmTimeout`` or ``LlmMalformedOutput``; never anything else."""
        ...
