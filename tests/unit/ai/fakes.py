"""A scripted stand-in for any language model provider."""

from typing import Any

from ai.llm.client import LlmError, StructuredRequest, StructuredResponse, Usage


class ScriptedLlm:
    """Answers with ``data`` (or raises ``error``) and records every request it received."""

    def __init__(self, data: Any = None, error: LlmError | None = None) -> None:
        self.data = data
        self.error = error
        self.requests: list[StructuredRequest] = []

    @property
    def name(self) -> str:
        return "scripted/test-model"

    async def complete(self, request: StructuredRequest) -> StructuredResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return StructuredResponse(self.data, Usage("scripted", "test-model", 120, 40, 5))


def proposal(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "capacity",
        "category": "concurrent_users",
        "title": "Concurrent connections",
        "quote": "2000 simultaneous sessions",
        "priority": "high",
        "confidence": 0.8,
        "metric": "concurrent_users",
        "operator": ">=",
        "value": "2000",
        "unit": "users",
    }
    return item | overrides
