"""The knowledge API against a real database (Knowledge/RAG Engine, phase 6): sources registered and
indexed in one transaction; unchanged content creates nothing; a changed one is the next version; a
failure keeps the version in force; record snapshots go stale and are re-read; search returns cited
passages of the version in force of the project's active sources only — never another project's;
secrets never stored; stored records append-only; authorized per project, tenant-isolated."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import KnowledgeChunkRecord, KnowledgeSourceRecord

from .requirement_support import World, create, member, signed_in

pytestmark = pytest.mark.integration

RUNBOOK = """\
# Orders runbook

Orders run on `orders-api` with PostgreSQL (ADR-3).

## Failover

Promote the read replica within 30 s. Replicas lag at most 2 s.

## Backups

Backups run nightly and are kept 30 days.
db_password: hunter2
"""


def sources(world: World, project_id: str | None = None) -> str:
    return f"/api/v1/projects/{project_id or world.project_id}/knowledge-sources"


def search_url(world: World, project_id: str | None = None) -> str:
    return f"/api/v1/projects/{project_id or world.project_id}/knowledge/search"


async def register(
    client: AsyncClient, world: World, body: dict[str, Any], project_id: str | None = None
) -> Any:
    response = await client.post(sources(world, project_id), json=body, headers=world.ada)
    assert response.status_code == 201, response.text
    return response.json()


def document(content: str = RUNBOOK, path: str = "docs/runbook.md") -> dict[str, Any]:
    return {"name": "Runbook", "document": {"path": path, "content": content}}


async def search(client: AsyncClient, world: World, **body: Any) -> Any:
    response = await client.post(search_url(world), json=body, headers=world.ada)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_document_is_indexed_searched_and_cited(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    registered = await register(client, world, document())
    source, run = registered["source"], registered["run"]
    assert (source["status"], source["indexedVersion"], source["contentType"]) == (
        "indexed",
        1,
        "text/markdown",
    )
    assert (run["status"], run["counts"]["chunks"], run["stage"]) == ("completed_with_warnings", 3, "done")
    assert run["warnings"] == ["1 secret-looking value was redacted before indexing."]

    found = await search(client, world, text="how fast is replica failover")
    passage = found["passages"][0]
    assert passage["citation"]["reference"] == "Runbook (v1): Orders runbook > Failover (lines 5-7)"
    assert (passage["method"], passage["verification"], passage["rank"]) == ("lexical", "user_provided", 1)
    assert "score" not in passage
    named = await search(client, world, identifiers=["ADR-3"])
    assert named["passages"][0]["matched"] == ["ADR-3"]
    nothing = await search(client, world, text="kafka partition rebalancing")
    assert (nothing["insufficientEvidence"], nothing["passages"]) == (True, [])

    chunk_id = passage["citation"]["chunkId"]
    one = await client.get(f"{sources(world)}/{source['id']}/passages/{chunk_id}", headers=world.ada)
    assert (one.status_code, one.json()["text"]) == (200, passage["text"])
    listed = (await client.get(sources(world), headers=world.ada)).json()
    assert [s["id"] for s in listed["sources"]] == [source["id"]]
    runs = (await client.get(f"{sources(world)}/{source['id']}/ingestions", headers=world.ada)).json()
    assert [r["id"] for r in runs["runs"]] == [run["id"]]
    for shown in (str(registered), str(found), str(named)):
        assert "hunter2" not in shown
    stored = await db.scalars(select(KnowledgeChunkRecord.text))
    assert all("hunter2" not in t for t in stored)


async def test_versions_unchanged_and_failures(client: AsyncClient, world: World) -> None:
    source = (await register(client, world, document()))["source"]
    url = f"{sources(world)}/{source['id']}/ingestions"
    same = await client.post(url, json={"content": RUNBOOK.replace("\n", "\r\n")}, headers=world.ada)
    assert (same.status_code, same.json()["run"]["status"], same.json()["source"]["indexedVersion"]) == (
        201, "unchanged", 1,
    )  # fmt: skip
    changed = await client.post(url, json={"content": RUNBOOK.replace("30 s", "60 s")}, headers=world.ada)
    assert changed.json()["source"]["indexedVersion"] == 2
    broken = await client.post(url, json={"content": "bad " + chr(0) + " byte"}, headers=world.ada)
    run = broken.json()["run"]
    assert (run["status"], run["stage"], run["errors"][0]["code"]) == (
        "failed",
        "extracting",
        "invalid_characters",
    )
    assert broken.json()["source"]["indexedVersion"] == 2  # the version in force survives
    retried = await client.post(url, json={"content": RUNBOOK, "retryOf": run["id"]}, headers=world.ada)
    assert (retried.json()["run"]["retryOf"], retried.json()["source"]["indexedVersion"]) == (run["id"], 3)
    found = await search(client, world, text="replica failover")
    assert found["passages"][0]["citation"]["sourceVersion"] == 3  # only the version in force
    first = await register(client, world, document(path="docs/other.md"))
    again = await client.post(sources(world), json=document(path="docs/other.md"), headers=world.ada)
    assert (again.status_code, again.json()["error"]["details"]["sourceId"]) == (409, first["source"]["id"])


async def test_a_requirement_snapshot_goes_stale_and_is_re_read(client: AsyncClient, world: World) -> None:
    requirement = await create(client, world)
    registered = await register(client, world, {"record": {"type": "requirement", "id": requirement["id"]}})
    source = registered["source"]
    assert (source["name"], source["record"]["version"], source["status"]) == ("REQ-1", 1, "indexed")
    changed = await client.patch(
        f"{world.base}/{requirement['id']}",
        json={
            "expectedVersion": 1,
            "statement": "Support 5,000 requests per second.",
            "changeReason": "Growth",
        },
        headers=world.ada,
    )
    assert changed.status_code == 200, changed.text
    stale = (await client.get(f"{sources(world)}/{source['id']}", headers=world.ada)).json()
    assert stale["status"] == "stale"
    old = await search(client, world, text="requests per second")
    assert old["passages"][0]["stale"]
    reread = await client.post(f"{sources(world)}/{source['id']}/ingestions", json={}, headers=world.ada)
    body = reread.json()["source"]
    assert (body["status"], body["indexedVersion"], body["record"]["version"]) == ("indexed", 2, 2)
    with_content = await client.post(
        f"{sources(world)}/{source['id']}/ingestions", json={"content": "x"}, headers=world.ada
    )
    assert with_content.json()["error"]["details"]["reason"] == "records_are_read_from_architectos"


async def test_archived_sources_are_kept_but_never_searched(client: AsyncClient, world: World) -> None:
    source = (await register(client, world, document()))["source"]
    archived = await client.post(f"{sources(world)}/{source['id']}/archive", headers=world.ada)
    assert archived.json()["lifecycle"] == "archived"
    assert (await search(client, world, text="replica failover"))["insufficientEvidence"]
    url = f"{sources(world)}/{source['id']}/ingestions"
    again = await client.post(url, json={"content": RUNBOOK}, headers=world.ada)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "invalid_knowledge_transition")
    listed = await client.get(sources(world), params={"lifecycle": "archived"}, headers=world.ada)
    assert [s["id"] for s in listed.json()["sources"]] == [source["id"]]
    renewed = await register(client, world, document())  # the path is free again
    assert renewed["source"]["id"] != source["id"]


async def test_authorization_and_tenant_isolation(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    source = (await register(client, world, document()))["source"]
    theirs = await register(
        client, world, document(RUNBOOK + "\nFailover drill on Fridays.\n"), world.other_id
    )
    found = await search(client, world, text="failover drill fridays")
    assert all(p["citation"]["sourceId"] == source["id"] for p in found["passages"])  # never the other's
    viewer = await member(client, db, outbox, world, "viewer")
    assert (await client.post(search_url(world), json={"text": "replica"}, headers=viewer)).status_code == 200
    assert (await client.get(f"{sources(world)}/{source['id']}", headers=viewer)).status_code == 200
    for method, path, body in (
        ("post", sources(world), document(path="docs/v.md")),
        ("post", f"{sources(world)}/{source['id']}/ingestions", {"content": RUNBOOK}),
        ("post", f"{sources(world)}/{source['id']}/archive", None),
    ):
        denied = await client.request(method, path, json=body, headers=viewer)
        assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    stranger = await signed_in(client, outbox, "eve@example.com")
    for path in (sources(world), f"{sources(world)}/{source['id']}"):
        assert (await client.get(path, headers=stranger)).json()["error"]["code"] == "project_not_found"
    leaked = await client.post(search_url(world), json={"text": "replica"}, headers=stranger)
    assert leaked.json()["error"]["code"] == "project_not_found"
    other = theirs["source"]["id"]
    hidden = await client.get(f"{sources(world)}/{other}", headers=world.ada)
    assert hidden.json()["error"]["code"] == "knowledge_source_not_found"  # the right id, another project
    narrowed = await search(client, world, text="replica failover", sourceIds=[other])
    assert narrowed["insufficientEvidence"]  # a filter never widens the scope


async def test_invalid_requests_store_nothing(client: AsyncClient, db: AsyncSession, world: World) -> None:
    record = {"type": "decision", "id": world.project_id}
    cases: list[tuple[dict[str, Any], str]] = [
        (document(path="../etc/passwd.md"), "invalid_knowledge_request"),
        (document(path="slides.pdf"), "invalid_knowledge_request"),
        ({"document": {"path": "a.md", "content": "x" * (512 * 1024 + 1)}}, "validation_error"),
        ({**document(), "record": record}, "invalid_knowledge_request"),
        ({**document(), "score": 1}, "validation_error"),
    ]
    for body, code in cases:
        response = await client.post(sources(world), json=body, headers=world.ada)
        assert response.json()["error"]["code"] == code, body
    missing = await client.post(sources(world), json={"record": record}, headers=world.ada)
    assert missing.json()["error"]["code"] == "decision_not_found"
    everything = await client.post(search_url(world), json={}, headers=world.ada)
    assert everything.status_code == 422  # terms or identifiers: never "everything"
    assert await db.scalar(select(func.count()).select_from(KnowledgeSourceRecord)) == 0


async def test_stored_records_are_kept_as_written(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    source = (await register(client, world, document()))["source"]
    params = {"id": source["id"]}
    for statement, message in (
        ("UPDATE knowledge_chunks SET text = 'x' WHERE source_id = :id", "append-only"),
        ("DELETE FROM knowledge_ingestion_runs WHERE source_id = :id", "append-only"),
        ("UPDATE knowledge_source_versions SET checksum = checksum WHERE source_id = :id", "append-only"),
        ("DELETE FROM knowledge_sources WHERE id = :id", "is kept"),
        ("UPDATE knowledge_sources SET path = 'x.md' WHERE id = :id", "identity of a source never changes"),
        ("UPDATE knowledge_sources SET indexed_version = NULL, indexed_checksum = NULL, status = 'pending' "
         "WHERE id = :id", "never goes back"),
    ):  # fmt: skip
        with pytest.raises(DBAPIError, match=message):
            async with db.begin_nested():
                await db.execute(text(statement), params)
    await client.post(f"{sources(world)}/{source['id']}/archive", headers=world.ada)
    unarchive = "UPDATE knowledge_sources SET lifecycle = 'active', archived_at = NULL WHERE id = :id"
    with pytest.raises(DBAPIError, match="stays archived"):
        async with db.begin_nested():
            await db.execute(text(unarchive), params)
