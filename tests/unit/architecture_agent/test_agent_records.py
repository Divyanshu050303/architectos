import uuid
from typing import Any

import pytest

from ai.llm.client import LlmTimeout
from core.domain.architecture_agent.errors import InvalidAgentRecord
from core.domain.architecture_agent.proposals import Answer
from core.domain.architecture_agent.records import run_document, run_from
from core.domain.architecture_agent.requests import AgentRequest, BaseRevision
from core.domain.architecture_agent.runs import AcceptedRevision, AgentRun
from core.domain.architecture_agent.values import RunStatus
from tests.unit.ai.test_architecture_agent import SequencedLlm, output
from tests.unit.architecture_agent.test_agent_pipeline import (
    LATENCY,
    NOW,
    USER,
    a_run,
    inputs,
    pipeline,
)


def stored(run: AgentRun) -> AgentRun:
    """Through the stored form and back, as the repository does."""
    return run_from(run_document(run))


async def ready() -> AgentRun:
    return await pipeline(SequencedLlm(output())).advance(a_run(), inputs())


async def test_a_ready_run_survives_storage() -> None:
    run = await ready()
    assert run.candidate is not None
    again = stored(run)
    assert again == run
    assert again.candidate is not None
    assert again.candidate.content_hash == run.candidate.content_hash


async def test_waiting_failed_and_decided_runs_survive_storage() -> None:
    waiting = await pipeline(SequencedLlm(output())).advance(a_run(), inputs((LATENCY,)))
    assert stored(waiting) == waiting
    answers = tuple(Answer(q.id, "300 rps", USER, NOW) for q in waiting.unanswered)
    answered = waiting.answer(answers, USER, NOW)
    assert stored(answered) == answered
    failed = await pipeline(SequencedLlm(LlmTimeout(), LlmTimeout())).advance(a_run(), inputs())
    assert stored(failed) == failed
    candidate = await ready()
    assert candidate.candidate is not None
    accepted = candidate.accept(
        AcceptedRevision(uuid.uuid4(), 1, candidate.candidate.content_hash), USER, NOW
    )
    assert stored(accepted) == accepted
    rejected = candidate.reject("Too large", USER, NOW)
    assert stored(rejected) == rejected
    assert stored(rejected).status is RunStatus.REJECTED


def test_an_iteration_keeps_its_base() -> None:
    base = BaseRevision(uuid.uuid4(), 4)
    run = a_run(request=AgentRequest(uuid.uuid4(), "Iterate", base=base))
    document = run_document(run)
    assert (document["base_architecture_id"], document["base_revision_number"]) == (base.architecture_id, 4)
    assert stored(run).request.base == base


async def test_only_a_hash_of_the_output_is_stored() -> None:
    run = await ready()
    document = run_document(run)
    assert not {"prompt", "context", "raw_output", "passages"} & set(document)
    assert run.raw_output is not None
    assert (document["raw_output_sha256"], document["raw_output_bytes"]) == (
        run.raw_output.sha256,
        run.raw_output.bytes,
    )


def _forge_question(d: dict[str, Any]) -> None:
    d["questions"][0]["id"] = "aq_forged"


@pytest.mark.parametrize(
    "tamper",
    [
        lambda d: d["candidate"].update(content_hash="0" * 64),  # not the candidate that was reviewed
        _forge_question,
        lambda d: d.update(status="unknown"),
        lambda d: d["candidate"]["ir"].update(nodes=[{"id": "x"}]),
        lambda d: d["usage"].pop("model_calls"),
    ],
)
async def test_a_tampered_record_is_refused(tamper: Any) -> None:
    document = run_document(await ready())
    tamper(document)
    with pytest.raises(InvalidAgentRecord):
        run_from(document)
