"""Architecture diff explanations in API tests: the real interpreter (retrieval, bounded context, the
agent's checks) with a scripted model in place of a provider — nothing leaves the process. The model
cites the first change it was given, so its output fits any diff."""

import re
from typing import Any

from ai.agents.diff_agent import DiffExplanationAgent
from ai.llm.client import StructuredRequest, StructuredResponse, Usage
from engines.architecture_diff.interpreter import DiffInterpretation

CHANGE = re.compile(r"\[(ch_[0-9a-f]{20})\]")


def explanation(change_id: str, **overrides: Any) -> dict[str, Any]:
    cited = {
        "text": "The target changes this element.",
        "groundings": [{"basis": "change", "ref": change_id}],
    }
    data: dict[str, Any] = {
        "summary": cited | {"inferred": False},
        "groups": [],
        "tradeoffs": [],
        "requirements": [],
        "risks": [{"text": "This may need a closer look.", "groundings": [], "inferred": True}],
        "questions": [
            {
                "text": "Was this change intended?",
                "groundings": [{"basis": "change", "ref": change_id}],
                "inferred": False,
            }
        ],
        "unknowns": [],
    }
    return data | overrides


class ContextLlm:
    """Explains whatever diff it is given by citing its first change; ``overrides`` replace parts of
    the output (e.g. to write something the agent must refuse). Records every request."""

    def __init__(self, **overrides: Any) -> None:
        self.overrides = overrides
        self.requests: list[StructuredRequest] = []

    @property
    def name(self) -> str:
        return "scripted/test-model"

    async def complete(self, request: StructuredRequest) -> StructuredResponse:
        self.requests.append(request)
        found = CHANGE.search(request.user_content)
        data = explanation(found.group(1) if found else "ch_" + "0" * 20, **self.overrides)
        return StructuredResponse(data, Usage("scripted", "test-model", 900, 200, 5))


def scripted_interpreter(llm: ContextLlm | None = None) -> DiffInterpretation:
    return DiffInterpretation(DiffExplanationAgent(llm or ContextLlm()))
