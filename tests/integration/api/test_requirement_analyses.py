"""The requirement analysis API (Requirements Engine phase 12; spec sections 39-41, 51)."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai.agents.requirement_agent import RequirementExtractionAgent
from ai.llm.client import LlmMalformedOutput, LlmTimeout, LlmUnavailable
from apps.api.email.transport import InMemoryTransport
from engines.requirements.service import ENGINE_VERSION, RequirementsEngine
from tests.unit.ai.fakes import ScriptedLlm, proposal
from tests.unit.engines import scenarios

from .requirement_support import World, create, member, signed_in

pytestmark = pytest.mark.integration


def analyses_url(world: World) -> str:
    return f"/api/v1/projects/{world.project_id}/requirement-analyses"


async def analyze(client: AsyncClient, world: World, text: str, status: int = 201) -> dict[str, Any]:
    response = await client.post(analyses_url(world), json={"input": text}, headers=world.ada)
    assert response.status_code == status, response.text
    result: dict[str, Any] = response.json()
    return result


# --- analyze ---------------------------------------------------------------------------------------


async def test_a_well_specified_description(client: AsyncClient, world: World) -> None:
    text = scenarios.FOOD_DELIVERY + "\n"
    analysis = await analyze(client, world, text)
    assert analysis["input"] == text  # exactly as written
    assert (analysis["engineVersion"], analysis["resultSchema"]) == (ENGINE_VERSION, 1)
    assert analysis["readyForArchitecture"] is True
    assert analysis["completeness"]["status"] == "complete"
    assert (analysis["semantic"]["used"], analysis["semantic"]["reason"]) == (False, "not_configured")
    candidate = analysis["candidates"][0]
    assert set(candidate) == {
        "key", "method", "source", "confidence", "span", "type", "category", "scope", "title",
        "statement", "priority", "structuredData", "normalizedData",
    }  # fmt: skip
    assert text[candidate["span"]["start"] : candidate["span"]["end"]] == candidate["span"]["text"]
    # Nothing became a requirement.
    assert (await client.get(world.base, headers=world.ada)).json()["requirements"] == []
    # And it can be read back unchanged.
    again = await client.get(f"{analyses_url(world)}/{analysis['id']}", headers=world.ada)
    assert again.json() == analysis


async def test_an_ambiguous_description(client: AsyncClient, world: World) -> None:
    analysis = await analyze(
        client, world, "The system should handle a lot of traffic and be fast. We have 10M users."
    )
    codes = {f["code"] for f in analysis["ambiguities"]}
    assert {"vague_traffic", "vague_latency", "user_count_kind_unspecified"} <= codes
    assert [c for c in analysis["candidates"] if c["structuredData"]] == []  # nothing invented
    assert analysis["readyForArchitecture"] is False


async def test_a_conflicting_description(client: AsyncClient, world: World) -> None:
    analysis = await analyze(
        client,
        world,
        "A food delivery platform. At least 10,000 rps. At most 5,000 rps. p95 under 300 ms. 99.9% uptime.",
    )
    [conflict] = [f for f in analysis["conflicts"] if f["kind"] == "conflict"]
    assert (conflict["code"], conflict["severity"]) == ("disjoint_bounds", "blocking")
    assert analysis["blocking"] == [conflict["key"]]
    assert analysis["readyForArchitecture"] is False


async def test_existing_requirements_take_part(client: AsyncClient, world: World) -> None:
    existing = await create(client, world)  # >= 2000 requests/second, active
    analysis = await analyze(client, world, "It must handle at most 1,000 requests per second.")  # same scope
    [conflict] = [f for f in analysis["conflicts"] if f["kind"] == "conflict"]
    assert conflict["requirementReferences"] == [f"{existing['reference']}@v1"]


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"input": ""}, 422, "invalid_requirement_input"),
        ({"input": "   \n"}, 422, "invalid_requirement_input"),
        ({"input": "a\u0000b"}, 422, "invalid_requirement_input"),
        ({"input": "x" * 20_001}, 422, "validation_error"),
        ({}, 422, "validation_error"),
        ({"input": 42}, 422, "validation_error"),
        ({"input": "ok", "engine": "llm"}, 422, "validation_error"),
    ],
    ids=["empty", "blank", "nul", "too-long", "missing", "not-text", "extra-field"],
)
async def test_invalid_input(
    client: AsyncClient, world: World, body: dict[str, Any], status: int, code: str
) -> None:
    response = await client.post(analyses_url(world), json=body, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)


async def test_a_very_large_body_is_refused_before_it_is_read(client: AsyncClient, world: World) -> None:
    response = await client.post(analyses_url(world), json={"input": "é" * 40_000}, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (413, "payload_too_large")


# --- authorization ---------------------------------------------------------------------------------


async def test_who_may_analyze_read_and_promote(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    analysis = await analyze(client, world, "Support at least 2000 rps.")
    viewer = await member(client, db, outbox, world, "viewer")
    eve = await signed_in(client, outbox, "eve@example.com")
    url = f"{analyses_url(world)}/{analysis['id']}"
    promote = {"candidateKeys": [analysis["candidates"][0]["key"]]}

    assert (await client.get(url, headers=viewer)).status_code == 200
    assert (await client.post(analyses_url(world), json={"input": "x"}, headers=viewer)).status_code == 403
    assert (await client.post(f"{url}/promote", json=promote, headers=viewer)).status_code == 403
    for response in (
        await client.get(url, headers=eve),
        await client.post(analyses_url(world), json={"input": "x"}, headers=eve),
        await client.post(f"{url}/promote", json=promote, headers=eve),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (404, "project_not_found")
    other = f"/api/v1/projects/{world.other_id}/requirement-analyses/{analysis['id']}"
    assert (await client.get(other, headers=world.ada)).json()["error"][
        "code"
    ] == "requirement_analysis_not_found"
    missing = await client.get(f"{analyses_url(world)}/{uuid.uuid4()}", headers=world.ada)
    assert missing.json()["error"]["code"] == "requirement_analysis_not_found"


async def test_archived_projects_cannot_be_analyzed_or_promoted_into(
    client: AsyncClient, world: World
) -> None:
    analysis = await analyze(client, world, "Support at least 2000 rps.")
    await client.post(f"/api/v1/projects/{world.project_id}/archive", headers=world.ada)
    blocked = [
        await client.post(analyses_url(world), json={"input": "Support 5 rps."}, headers=world.ada),
        await client.post(
            f"{analyses_url(world)}/{analysis['id']}/promote",
            json={"candidateKeys": [analysis["candidates"][0]["key"]]},
            headers=world.ada,
        ),
    ]
    assert [(r.status_code, r.json()["error"]["code"]) for r in blocked] == [(409, "project_archived")] * 2
    assert (await client.get(f"{analyses_url(world)}/{analysis['id']}", headers=world.ada)).status_code == 200


# --- promote ---------------------------------------------------------------------------------------


async def test_promotion_creates_drafts_with_provenance_and_is_idempotent(
    client: AsyncClient, world: World
) -> None:
    analysis = await analyze(client, world, scenarios.FOOD_DELIVERY)
    keys = [c["key"] for c in analysis["candidates"][:3]]
    url = f"{analyses_url(world)}/{analysis['id']}/promote"

    first = await client.post(url, json={"candidateKeys": keys}, headers=world.ada)
    assert first.status_code == 200, first.text
    promotions = first.json()["promotions"]
    assert [(p["candidateKey"], p["created"]) for p in promotions] == [(k, True) for k in keys]
    for promotion, candidate in zip(promotions, analysis["candidates"], strict=False):
        requirement = promotion["requirement"]
        assert (requirement["status"], requirement["source"]) == ("draft", "system")
        assert requirement["structuredData"] == candidate["structuredData"]
        assert requirement["statement"] == candidate["statement"]

    retry = await client.post(url, json={"candidateKeys": keys}, headers=world.ada)
    assert [p["created"] for p in retry.json()["promotions"]] == [False, False, False]
    assert [p["requirement"]["id"] for p in retry.json()["promotions"]] == [
        p["requirement"]["id"] for p in promotions
    ]
    listed = (await client.get(world.base, headers=world.ada)).json()["requirements"]
    assert len(listed) == 3

    # The planning input of a set traces each requirement back to the analysis and candidate.
    for promotion in promotions:
        await client.patch(
            f"{world.base}/{promotion['requirement']['id']}",
            json={"expectedVersion": 1, "status": "active"},
            headers=world.ada,
        )
    requirement_set = await client.post(
        f"/api/v1/projects/{world.project_id}/requirement-sets", json={}, headers=world.ada
    )
    planning = await client.get(
        f"/api/v1/projects/{world.project_id}/requirement-sets/{requirement_set.json()['id']}/planning-input",
        headers=world.ada,
    )
    origins = [r["origin"] for r in planning.json()["planningInput"]["requirements"]]
    assert origins == [{"analysis_id": analysis["id"], "candidate_key": key} for key in keys]


@pytest.mark.parametrize(
    ("keys", "status", "code"),
    [
        ([], 422, "invalid_promotion"),
        (["cand_0000000000000000"], 422, "invalid_promotion"),
        (["x"] * 101, 422, "validation_error"),
        (["x" * 65], 422, "validation_error"),
    ],
    ids=["empty", "unknown", "too-many", "too-long"],
)
async def test_invalid_promotions(
    client: AsyncClient, world: World, keys: list[str], status: int, code: str
) -> None:
    analysis = await analyze(client, world, "Support at least 2000 rps.")
    response = await client.post(
        f"{analyses_url(world)}/{analysis['id']}/promote", json={"candidateKeys": keys}, headers=world.ada
    )
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)


# --- the language model, through the API -----------------------------------------------------------


@pytest.mark.parametrize(
    ("llm", "status"),
    [
        (ScriptedLlm(error=LlmUnavailable()), "llm_unavailable"),
        (ScriptedLlm(error=LlmTimeout()), "llm_timeout"),
        (ScriptedLlm(error=LlmMalformedOutput()), "llm_malformed_output"),
        (ScriptedLlm({"requirements": "garbage"}), "llm_malformed_output"),
    ],
    ids=["unavailable", "timeout", "malformed", "wrong-shape"],
)
async def test_a_failing_model_never_fails_the_analysis(
    app: FastAPI, client: AsyncClient, world: World, llm: ScriptedLlm, status: str
) -> None:
    app.state.requirements_engine = RequirementsEngine(RequirementExtractionAgent(llm))
    analysis = await analyze(
        client, world, "Support at least 2000 rps. We keep 2000 simultaneous sessions open."
    )
    assert analysis["semantic"]["status"] == status
    assert analysis["engineVersion"] == ENGINE_VERSION
    assert [c["source"] for c in analysis["candidates"]] == ["system"]
    assert any(f["kind"] == "extraction" and f["code"] == status for f in analysis["issues"])


async def test_model_proposals_are_validated_drafts(app: FastAPI, client: AsyncClient, world: World) -> None:
    llm = ScriptedLlm(
        {"requirements": [proposal(quote="2000 simultaneous sessions"), proposal(value="-1", quote="open")]}
    )
    app.state.requirements_engine = RequirementsEngine(RequirementExtractionAgent(llm))
    analysis = await analyze(client, world, "We keep 2000 simultaneous sessions open at peak.")
    [ai] = [c for c in analysis["candidates"] if c["source"] == "ai"]
    assert (ai["method"], ai["confidence"]) == ("llm", "0.8")
    assert [f["code"] for f in analysis["issues"] if f["kind"] == "rejected"] == ["llm_proposal_invalid"]
    [promotion] = (
        await client.post(
            f"{analyses_url(world)}/{analysis['id']}/promote",
            json={"candidateKeys": [ai["key"]]},
            headers=world.ada,
        )
    ).json()["promotions"]
    assert (promotion["requirement"]["source"], promotion["requirement"]["status"]) == ("ai", "draft")
    assert promotion["requirement"]["confidence"] == "0.8"
