"""The Anthropic adapter, against a mocked HTTP transport (no network)."""

import json
from typing import Any

import httpx2 as httpx  # the SDK is built on httpx2, which keeps the httpx API
import pytest
from anthropic import AsyncAnthropic

from ai.llm.client import LlmMalformedOutput, LlmTimeout, LlmUnavailable, StructuredRequest
from ai.llm.providers.anthropic import AnthropicStructuredLlm

REQUEST = StructuredRequest(
    system="sys", user_content="data", schema={"type": "object"}, max_output_tokens=100, timeout_seconds=3
)


def llm(handler: Any) -> AnthropicStructuredLlm:
    client = AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return AnthropicStructuredLlm(api_key="unused", model="claude-sonnet-5", client=client)


def message(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 321, "output_tokens": 45},
    }


async def test_a_structured_answer() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=message('{"requirements": []}'))

    response = await llm(handler).complete(REQUEST)
    assert response.data == {"requirements": []}
    assert (response.usage.provider, response.usage.model) == ("anthropic", "claude-sonnet-5")
    assert (response.usage.input_tokens, response.usage.output_tokens) == (321, 45)
    [body] = sent
    assert body["system"] == "sys"
    assert body["messages"] == [{"role": "user", "content": "data"}]
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert "tools" not in body
    assert llm(handler).name == "anthropic/claude-sonnet-5"


@pytest.mark.parametrize(
    ("handler", "error"),
    [
        (
            lambda r: httpx.Response(
                529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
            ),
            LlmUnavailable,
        ),
        (
            lambda r: httpx.Response(
                429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "x"}}
            ),
            LlmUnavailable,
        ),
        (
            lambda r: httpx.Response(
                401, json={"type": "error", "error": {"type": "authentication_error", "message": "x"}}
            ),
            LlmUnavailable,
        ),
        (lambda r: httpx.Response(200, json=message("not json")), LlmMalformedOutput),
        (lambda r: httpx.Response(200, json=message('{"requirements": [', "max_tokens")), LlmMalformedOutput),
    ],
    ids=["overloaded", "rate-limited", "bad-key", "not-json", "cut-off"],
)
async def test_failures_map_to_the_port_errors(handler: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        await llm(handler).complete(REQUEST)


async def test_timeouts_and_connection_failures() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LlmTimeout):
        await llm(timeout).complete(REQUEST)
    with pytest.raises(LlmUnavailable):
        await llm(refused).complete(REQUEST)


async def test_the_api_key_never_appears_in_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"type": "error", "error": {"type": "authentication_error", "message": "bad"}}
        )

    client = AsyncAnthropic(
        api_key="sk-secret-123",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(LlmUnavailable) as caught:
        await AnthropicStructuredLlm(api_key="sk-secret-123", client=client).complete(REQUEST)
    assert "sk-secret-123" not in str(caught.value)
