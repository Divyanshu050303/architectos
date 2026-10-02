"""The discovery API against a real database (Discovery Engine, phase 8): runs stored with their result
(never the artifacts' content, never a secret), read back verified, reviewed by authorized people, a
proposal accepted only explicitly and only through the architecture workflow — never rewriting a
revision — compared, deleted unless accepted, tenant-isolated."""

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from engines.discovery import engine as engine_module
from persistence.models import ArchitectureRevisionRecord, DiscoveryRunRecord
from persistence.repositories.discovery import SqlAlchemyDiscoveryRunRepository
from tests.unit.discovery.test_discovery_sources import COMPOSE, DEPLOYMENT, TERRAFORM

from .requirement_support import World, member, signed_in

pytestmark = pytest.mark.integration

SECRET = "pa55word"  # in COMPOSE's DATABASE_URL: never stored, never returned
DB_NODE = "compose:shop/service/db"
API_NODE = "compose:shop/service/api"


def runs(world: World, project_id: str | None = None) -> str:
    return f"/api/v1/projects/{project_id or world.project_id}/discovery-runs"


def body(*artifacts: tuple[str, str], **extra: Any) -> dict[str, Any]:
    return {"artifacts": [{"path": p, "content": c} for p, c in artifacts]} | extra


def decision(subject: str, verdict: str = "accepted", **chosen: str) -> dict[str, str]:
    return {"subjectType": "entity", "subject": subject, "decision": verdict} | chosen


async def discover(
    client: AsyncClient, world: World, *artifacts: tuple[str, str], **extra: Any
) -> dict[str, Any]:
    response = await client.post(runs(world), json=body(*artifacts, **extra), headers=world.ada)
    assert response.status_code == 201, response.text
    run: dict[str, Any] = response.json()
    return run


async def proposal(client: AsyncClient, world: World, run_id: str) -> dict[str, Any]:
    response = await client.get(f"{runs(world)}/{run_id}/proposal", headers=world.ada)
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


async def architecture(client: AsyncClient, world: World) -> str:
    ir = {"schema_version": 1, "name": "Shop", "nodes": [{"id": DB_NODE, "kind": "database", "name": "db"}]}
    created = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures",
        json={"name": "Shop", "ir": ir},
        headers=world.ada,
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def first_revision_hash(db: AsyncSession, architecture_id: str) -> str | None:
    found: str | None = await db.scalar(
        select(ArchitectureRevisionRecord.content_hash).where(
            ArchitectureRevisionRecord.architecture_id == uuid.UUID(architecture_id),
            ArchitectureRevisionRecord.number == 1,
        )
    )
    return found


async def test_a_run_is_stored_without_content_or_secrets_and_read_back(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    artifacts = (
        ("compose.yaml", COMPOSE),
        ("k8s/shop.yaml", DEPLOYMENT),
        ("main.tf.json", json.dumps(TERRAFORM)),
    )
    run = await discover(client, world, *artifacts, label="Shop")
    assert run["status"] == "completed_with_warnings"  # kinds to review, unresolved references
    extractors = {a["path"]: a["extractor"] for a in run["artifacts"]}
    assert extractors == {
        "compose.yaml": "docker_compose@1",
        "k8s/shop.yaml": "kubernetes@1",
        "main.tf.json": "terraform_json@1",
    }
    db_entity = next(e for e in run["entities"] if e["key"] == DB_NODE)
    assert (db_entity["kind"], db_entity["mapping"]["componentId"]) == ("database", "databases/postgresql")
    read = (await client.get(f"{runs(world)}/{run['id']}", headers=world.ada)).json()
    assert read == run  # verified against its fingerprint when read back
    findings = (await client.get(f"{runs(world)}/{run['id']}/findings", headers=world.ada)).json()["findings"]
    password = next(f for f in findings if f["sourceProperty"] == "password")
    assert (password["redacted"], password["value"]) == (True, None)  # its presence only
    record = await db.scalar(select(DiscoveryRunRecord).where(DiscoveryRunRecord.id == uuid.UUID(run["id"])))
    assert record is not None
    stored = json.dumps([record.result, record.summary, record.decisions])
    for secret in (SECRET, "s3cr3t", "hunter2", "sk_live_do_not_keep", "apiVersion: apps/v1"):
        assert secret not in stored  # neither a secret nor the artifacts' content
        assert secret not in json.dumps([run, findings])


async def test_review_and_explicit_acceptance_create_a_new_architecture(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    run = await discover(client, world, ("compose.yaml", COMPOSE))
    url = f"{runs(world)}/{run['id']}"
    decided = await client.post(
        f"{url}/decisions", json=decision(API_NODE, nodeKind="service"), headers=world.ada
    )
    assert decided.status_code == 200, decided.text
    overriding = await client.post(
        f"{url}/decisions", json=decision(DB_NODE, nodeKind="cache"), headers=world.ada
    )
    assert (overriding.status_code, overriding.json()["error"]["details"]["reason"]) == (
        422, "stated_by_the_source",
    )  # fmt: skip
    reviewed = await proposal(client, world, run["id"])
    assert {n["id"] for n in reviewed["architecture"]["nodes"]} == {API_NODE, DB_NODE}
    stale = await client.post(f"{url}/accept", json={"proposalContentHash": "0" * 64}, headers=world.ada)
    assert stale.json()["error"]["details"]["reason"] == "proposal_changed"
    accept = {"proposalContentHash": reviewed["contentHash"], "name": "Shop (discovered)"}
    accepted = await client.post(f"{url}/accept", json=accept, headers=world.ada)
    assert accepted.status_code == 201, accepted.text
    created = accepted.json()
    assert (created["revision"], created["createdArchitecture"]) == (1, True)
    assert created["contentHash"] == reviewed["contentHash"]  # exactly what was reviewed
    revision = await db.scalar(
        select(ArchitectureRevisionRecord).where(
            ArchitectureRevisionRecord.architecture_id == uuid.UUID(created["architectureId"])
        )
    )
    assert revision is not None
    assert revision.source == "discovery"
    assert str(run["id"]) in (revision.reason or "")
    recorded = (await client.get(url, headers=world.ada)).json()["accepted"]
    assert recorded[0]["architectureId"] == created["architectureId"]
    kept = await client.delete(url, headers=world.ada)
    assert (kept.status_code, kept.json()["error"]["code"]) == (409, "accepted_discovery_run")


async def test_accepting_into_an_architecture_adds_a_revision_and_never_rewrites_one(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid = await architecture(client, world)
    first = await first_revision_hash(db, aid)
    run = await discover(
        client, world, ("compose.yaml", COMPOSE), baseline={"architectureId": aid, "revision": 1}
    )
    assert run["baseline"] == {"architectureId": aid, "revision": 1}
    compared = (await client.get(f"{runs(world)}/{run['id']}/baseline-comparison", headers=world.ada)).json()
    assert compared["comparability"] == "partially_comparable"
    reviewed = await proposal(client, world, run["id"])
    current = await client.get(f"/api/v1/projects/{world.project_id}/architectures/{aid}", headers=world.ada)
    assert current.json()["currentVersion"] == 1  # running and reviewing changed nothing
    accept = {"proposalContentHash": reviewed["contentHash"], "architectureId": aid}
    url = f"{runs(world)}/{run['id']}/accept"
    missing = await client.post(url, json=accept, headers=world.ada)
    assert missing.json()["error"]["details"] == {"field": "baseVersion", "reason": "required"}
    conflict = await client.post(url, json=accept | {"baseVersion": 7}, headers=world.ada)
    assert conflict.json()["error"]["code"] == "architecture_version_conflict"
    accepted = await client.post(url, json=accept | {"baseVersion": 1}, headers=world.ada)
    assert accepted.status_code == 201, accepted.text
    assert (accepted.json()["revision"], accepted.json()["createdArchitecture"]) == (2, False)
    assert await first_revision_hash(db, aid) == first  # history is never rewritten


async def test_access_isolation_and_listing(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    run = await discover(client, world, ("compose.yaml", COMPOSE))
    url = f"{runs(world)}/{run['id']}"
    viewer = await member(client, db, outbox, world, "viewer")
    assert (await client.get(url, headers=viewer)).status_code == 200
    assert (await client.get(f"{url}/proposal", headers=viewer)).status_code == 200
    writes: list[tuple[str, str, dict[str, Any] | None]] = [
        ("post", runs(world), body(("compose.yaml", COMPOSE))),
        ("post", f"{url}/decisions", decision(DB_NODE, "rejected")),
        ("post", f"{url}/accept", {"proposalContentHash": "0" * 64}),
        ("delete", url, None),
    ]
    for method, path, payload in writes:
        denied = await client.request(method, path, json=payload, headers=viewer)
        assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    stranger = await signed_in(client, outbox, "eve@example.com")
    for path in (url, runs(world), f"{url}/findings", f"{url}/proposal"):
        assert (await client.get(path, headers=stranger)).json()["error"]["code"] == "project_not_found"
    assert (await client.get(url)).status_code == 401
    hidden = await client.get(f"{runs(world, world.other_id)}/{run['id']}", headers=world.ada)
    assert hidden.json()["error"]["code"] == "discovery_run_not_found"  # another project's run
    elsewhere = body(
        ("compose.yaml", COMPOSE),
        baseline={"architectureId": await architecture(client, world), "revision": 1},
    )
    foreign = await client.post(runs(world, world.other_id), json=elsewhere, headers=world.ada)
    assert foreign.json()["error"]["code"] == "architecture_not_found"  # not of that project

    second = await discover(client, world, ("k8s/shop.yaml", DEPLOYMENT))
    listed = (await client.get(runs(world), params={"limit": 1}, headers=world.ada)).json()
    rest = (await client.get(runs(world), params={"cursor": listed["nextCursor"]}, headers=world.ada)).json()
    assert [r["id"] for r in listed["runs"] + rest["runs"]] == [second["id"], run["id"]]
    assert "entities" not in listed["runs"][0]  # listings never carry results
    failed = (await client.get(runs(world), params={"status": "failed"}, headers=world.ada)).json()
    assert failed["runs"] == []


async def test_invalid_requests_store_nothing_and_limits_store_a_failed_run(
    client: AsyncClient, db: AsyncSession, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    unsafe = await client.post(runs(world), json=body(("../etc/passwd", "x")), headers=world.ada)
    assert (unsafe.status_code, unsafe.json()["error"]["details"]["reason"]) == (422, "unsafe_path")
    many = body(*[(f"f{i}.yaml", "a: 1") for i in range(51)])
    assert (await client.post(runs(world), json=many, headers=world.ada)).status_code == 422
    assert await db.scalar(select(func.count()).select_from(DiscoveryRunRecord)) == 0
    monkeypatch.setattr(engine_module, "MAX_ENTITIES", 1)
    failed = await discover(client, world, ("compose.yaml", COMPOSE))
    assert (failed["status"], failed["error"]["code"], failed["entities"]) == (
        "failed",
        "too_many_entities",
        [],
    )
    refused = await client.get(f"{runs(world)}/{failed['id']}/proposal", headers=world.ada)
    assert refused.json()["error"]["code"] == "invalid_discovery_transition"


async def test_reruns_are_compared_on_what_both_read(client: AsyncClient, world: World) -> None:
    first = await discover(client, world, ("compose.yaml", COMPOSE))
    later = await discover(client, world, ("compose.yaml", COMPOSE.replace("postgres:16", "postgres:17")))
    url = f"{runs(world)}/{later['id']}/comparison"
    comparison = (await client.get(url, params={"with": first["id"]}, headers=world.ada)).json()
    assert comparison["comparability"] == "comparable"
    assert (comparison["earlier"], comparison["later"]) == (first["fingerprint"], later["fingerprint"])
    [difference] = comparison["differences"]
    assert difference["fields"] == [
        {"field": "technology_version", "before": "16", "after": "17", "change": "modified"}
    ]
    assert first["sourcesFingerprint"] != later["sourcesFingerprint"]


async def test_a_runs_result_never_changes_and_an_unaccepted_run_can_be_deleted(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    run = await discover(client, world, ("compose.yaml", COMPOSE))
    for statement, message in (
        ("UPDATE discovery_runs SET label = 'x' WHERE id = :id", "only the decisions and acceptances"),
        # CASCADE: the drift analyses' foreign key would refuse it first; the guards still do.
        ("TRUNCATE discovery_runs CASCADE", "cannot be truncated|append-only"),
    ):
        with pytest.raises(DBAPIError, match=message):
            async with db.begin_nested():
                await db.execute(text(statement), {"id": run["id"]})
    deleted = await client.delete(f"{runs(world)}/{run['id']}", headers=world.ada)
    assert deleted.status_code == 204
    assert (await client.get(f"{runs(world)}/{run['id']}", headers=world.ada)).status_code == 404


async def test_an_engine_failure_exposes_nothing_and_stores_nothing(
    app: FastAPI, client: AsyncClient, db: AsyncSession, world: World
) -> None:
    class Broken:
        def discover(self, request: Any, name: Any = None) -> Any:
            raise RuntimeError("internal detail sk_live_do_not_leak")

    app.state.discovery_engine = Broken()
    response = await client.post(runs(world), json=body(("compose.yaml", COMPOSE)), headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (500, "internal_error")
    assert "sk_live" not in json.dumps(response.json())
    assert await db.scalar(select(func.count()).select_from(DiscoveryRunRecord)) == 0


async def test_an_acceptance_that_cannot_be_recorded_leaves_no_revision(
    client: AsyncClient, db: AsyncSession, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = await discover(client, world, ("compose.yaml", COMPOSE))
    reviewed = await proposal(client, world, run["id"])
    before = await db.scalar(select(func.count()).select_from(ArchitectureRevisionRecord))

    async def broken(self: Any, run: Any) -> Any:
        raise RuntimeError("the acceptance cannot be stored")

    monkeypatch.setattr(SqlAlchemyDiscoveryRunRepository, "update_review", broken)
    response = await client.post(
        f"{runs(world)}/{run['id']}/accept",
        json={"proposalContentHash": reviewed["contentHash"]},
        headers=world.ada,
    )
    assert response.status_code == 500
    assert (
        await db.scalar(select(func.count()).select_from(ArchitectureRevisionRecord)) == before
    )  # rolled back
    assert (await client.get(f"{runs(world)}/{run['id']}", headers=world.ada)).json()["accepted"] == []
