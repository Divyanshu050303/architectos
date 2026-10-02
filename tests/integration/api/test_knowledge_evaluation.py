"""The evaluation's answers are the API's answers (Knowledge/RAG Engine, phase 7): the v1 corpus registered
through the API and every labelled query asked of PostgreSQL return the same passages — same source,
location, words, method and rank — as the in-memory evaluation, so the database prefilter changes
nothing the rules decide."""

from typing import Any

import pytest
from httpx import AsyncClient

from ai.evaluation.knowledge import corpus_files, evaluate, load_queries

from .requirement_support import World

pytestmark = pytest.mark.integration


def _shape(passage: dict[str, Any]) -> tuple[Any, ...]:
    citation = passage["citation"]
    locator = citation["locator"]
    return (
        citation["sourceName"], tuple(locator["headingPath"]), locator["lineStart"], locator["lineEnd"],
        passage["text"], passage["method"], passage["rank"],
    )  # fmt: skip


async def test_the_api_returns_what_the_evaluation_measures(client: AsyncClient, world: World) -> None:
    base = f"/api/v1/projects/{world.project_id}"
    sources: dict[str, str] = {}
    for path in corpus_files():
        body = {
            "name": path.name,
            "document": {"path": path.name, "content": path.read_text(encoding="utf-8")},
        }
        registered = await client.post(f"{base}/knowledge-sources", json=body, headers=world.ada)
        assert registered.status_code == 201, registered.text
        sources[path.name] = registered.json()["source"]["id"]

    expected = {o.id: o.result for o in evaluate().outcomes}
    for spec in load_queries():
        query = spec["query"]
        body = {
            "text": query.get("text"),
            "identifiers": query.get("identifiers", []),
            "sourceIds": [sources[name] for name in query.get("sources", [])],
            "limit": query.get("limit", 10),
        }
        response = await client.post(f"{base}/knowledge/search", json=body, headers=world.ada)
        assert response.status_code == 200, response.text
        got = [_shape(p) for p in response.json()["passages"]]
        want = [
            (
                p.citation.source_name, p.citation.locator.heading_path, p.citation.locator.line_start,
                p.citation.locator.line_end, p.text, p.method.value, p.rank,
            )
            for p in expected[spec["id"]].passages
        ]  # fmt: skip
        assert got == want, spec["id"]
        assert response.json()["insufficientEvidence"] == (not want), spec["id"]
