"""Anthropic, behind the ``StructuredLlm`` port: native JSON-schema output, no tools, no retries
(the engine degrades gracefully instead of waiting), a hard timeout, and every SDK failure mapped
to the port's errors. The API key is never logged or returned."""

import json
import time

import anthropic
from anthropic import AsyncAnthropic

from ai.llm.client import (
    LlmMalformedOutput,
    LlmTimeout,
    LlmUnavailable,
    StructuredRequest,
    StructuredResponse,
    Usage,
)

DEFAULT_MODEL = "claude-sonnet-5"


class AnthropicStructuredLlm:
    def __init__(
        self, *, api_key: str, model: str = DEFAULT_MODEL, client: AsyncAnthropic | None = None
    ) -> None:
        self._model = model
        self._client = client or AsyncAnthropic(api_key=api_key, max_retries=0)

    @property
    def name(self) -> str:
        return f"anthropic/{self._model}"

    async def complete(self, request: StructuredRequest) -> StructuredResponse:
        started = time.monotonic()
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=request.max_output_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user_content}],
                output_config={"format": {"type": "json_schema", "schema": request.schema}},
                timeout=request.timeout_seconds,
            )
        except anthropic.APITimeoutError as error:
            raise LlmTimeout from error
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as error:
            raise LlmUnavailable(type(error).__name__) from error
        except anthropic.AnthropicError as error:  # any other SDK failure
            raise LlmUnavailable(type(error).__name__) from error
        latency_ms = int((time.monotonic() - started) * 1000)
        if message.stop_reason == "max_tokens":
            raise LlmMalformedOutput("the answer was cut off")
        text = "".join(block.text for block in message.content if block.type == "text")
        try:
            data = json.loads(text)
        except ValueError as error:
            raise LlmMalformedOutput("not JSON") from error
        usage = Usage(
            provider="anthropic",
            model=self._model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            latency_ms=latency_ms,
        )
        return StructuredResponse(data=data, usage=usage)
