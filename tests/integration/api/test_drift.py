"""The drift API against a real database (Drift Detection Engine, phase 8): an analysis compares the
exact baseline revision with a stored discovery run and is stored append-only, verified when read back;
its findings fold into drift items that people review — never changing the architecture or the run;
identities are confirmed by people; everything is authorized per project and tenant-isolated."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import ArchitectureRevisionRecord, DriftAnalysisRecord

from .requirement_support import World, member, signed_in

pytestmark = pytest.mark.integration

DB = "compose:shop/service/db"
CACHE = "compose:shop/service/cache"
SECRET = "s3cr3t-v1"
ROTATED = "s3cr3t-v2"
V1 = f"""\
name: shop
services:
  db:
    image: postgres:16
    environment: [POSTGRES_PASSWORD={SECRET}]
"""
V2 = f"""\
name: shop
services:
  db:
    image: postgres:16
    environment: [POSTGRES_PASSWORD={ROTATED}]
    deploy: {{replicas: 3}}
  cache:
    image: redis:7
"""


def project(world: World, project_id: str | None = None) -> str:
    return f"/api/v1/projects/{project_id or world.project_id}"


async def discover(client: AsyncClient, world: World, content: str) -> str:
    run = await client.post(
        f"{project(world)}/discovery-runs",
        json={"artifacts": [{"path": "compose.yaml", "content": content}]},
        headers=world.ada,
    )
    assert run.status_code == 201, run.text
    return str(run.json()["id"])


async def accepted(client: AsyncClient, world: World) -> tuple[str, str]:
    """An architecture accepted from a discovery of V1, and that run."""
    run_id = await discover(client, world, V1)
    url = f"{project(world)}/discovery-runs/{run_id}"
    proposal = (await client.get(f"{url}/proposal", headers=world.ada)).json()
    accept = {"proposalContentHash": proposal["contentHash"], "name": "Shop"}
    response = await client.post(f"{url}/accept", json=accept, headers=world.ada)
    assert response.status_code == 201, response.text
    return str(response.json()["architectureId"]), run_id


async def analyze(
    client: AsyncClient, world: World, architecture_id: str, run_id: str, **extra: Any
) -> dict[str, Any]:
    body = {"architectureId": architecture_id, "baselineRevision": 1, "discoveryRunId": run_id} | extra
    response = await client.post(f"{project(world)}/drift-analyses", json=body, headers=world.ada)
    assert response.status_code == 201, response.text
    found: dict[str, Any] = response.json()
    return found


async def findings(client: AsyncClient, world: World, analysis_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"{project(world)}/drift-analyses/{analysis_id}/findings", headers=world.ada)
    assert response.status_code == 200, response.text
    found: list[dict[str, Any]] = response.json()["findings"]
    return found


async def items(client: AsyncClient, world: World, **params: str) -> list[dict[str, Any]]:
    response = await client.get(f"{project(world)}/drift-items", params=params, headers=world.ada)
    assert response.status_code == 200, response.text
    found: list[dict[str, Any]] = response.json()["items"]
    return found


async def revisions(db: AsyncSession, architecture_id: str) -> list[str]:
    hashes = await db.scalars(
        select(ArchitectureRevisionRecord.content_hash)
        .where(ArchitectureRevisionRecord.architecture_id == uuid.UUID(architecture_id))
        .order_by(ArchitectureRevisionRecord.number)
    )
    return list(hashes)


async def test_an_analysis_is_stored_read_back_and_changes_nothing(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid, baseline_run = await accepted(client, world)
    before = await revisions(db, aid)
    run_url = f"{project(world)}/discovery-runs/{baseline_run}"
    run_before = (await client.get(run_url, headers=world.ada)).json()

    same = await analyze(client, world, aid, baseline_run)
    assert same["summary"]["findings"] == 0
    assert same["summary"]["noDifferenceWithinCoverage"] is True  # within coverage, never "no drift"
    assert same["coverage"]["inspected"] == ["compose.yaml"]

    changed_run = await discover(client, world, V2)
    analysis = await analyze(client, world, aid, changed_run, label="Nightly")
    assert analysis["baseline"]["revisionNumber"] == 1
    assert analysis["baseline"]["contentHash"] == before[0]
    assert analysis["observed"]["discoveryRunId"] == changed_run
    assert "score" not in str(analysis["summary"]).lower()
    found = {(f["type"], f["subject"], f["path"]): f for f in await findings(client, world, analysis["id"])}
    added = found[("component_added", f"node:{CACHE}", None)]
    assert (added["classification"], added["locations"]) == ("confirmed", ["compose.yaml#0:services.cache"])
    replicas = found[("resource_changed", f"node:{DB}", "configuration.replicas")]
    assert (replicas["baselineValue"], replicas["discoveredValue"]) == (None, 3)
    assert replicas["match"] == "same_id"
    assert {i["engine"] for i in replicas["impact"]} >= {"capacity"}

    url = f"{project(world)}/drift-analyses/{analysis['id']}"
    assert (await client.get(f"{url}/findings/{added['id']}", headers=world.ada)).json() == added
    filtered = await client.get(f"{url}/findings", params={"type": "component_added"}, headers=world.ada)
    assert [f["id"] for f in filtered.json()["findings"]] == [added["id"]]
    missing = await client.get(f"{url}/findings/dft_{'0' * 20}", headers=world.ada)
    assert missing.status_code == 404

    # Neither side changed: no revision written, the run's result untouched.
    assert await revisions(db, aid) == before
    assert (await client.get(run_url, headers=world.ada)).json() == run_before
    assert (await client.get(url, headers=world.ada)).json() == analysis  # read back, verified

    listing = f"{project(world)}/drift-analyses"
    page = (await client.get(listing, params={"architectureId": aid, "limit": 1}, headers=world.ada)).json()
    rest = (await client.get(listing, params={"cursor": page["nextCursor"]}, headers=world.ada)).json()
    assert [a["id"] for a in page["analyses"] + rest["analyses"]] == [analysis["id"], same["id"]]


async def test_secrets_are_never_stored_or_returned(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid, _ = await accepted(client, world)
    analysis = await analyze(client, world, aid, await discover(client, world, V2))
    shown = str(await findings(client, world, analysis["id"])) + str(analysis)
    stored = await db.scalar(
        select(DriftAnalysisRecord.result).where(DriftAnalysisRecord.id == uuid.UUID(analysis["id"]))
    )
    for secret in (SECRET, ROTATED):
        assert secret not in shown
        assert secret not in str(stored)


async def test_items_follow_differences_and_their_review_never_changes_the_architecture(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid, baseline_run = await accepted(client, world)
    changed_run = await discover(client, world, V2)
    first = await analyze(client, world, aid, changed_run)
    again = await analyze(client, world, aid, changed_run)
    tracked = await items(client, world, architectureId=aid)
    assert len(tracked) == first["summary"]["findings"]  # detected twice, one item each
    cache = next(i for i in tracked if i["subject"] == f"node:{CACHE}")
    assert (cache["status"], cache["firstAnalysisId"], cache["lastAnalysisId"]) == (
        "open", first["id"], again["id"],
    )  # fmt: skip
    assert [e["action"] for e in cache["history"]] == ["detected", "detected"]
    assert cache["artifacts"] == ["compose.yaml"]
    before = await revisions(db, aid)

    url = f"{project(world)}/drift-items/{cache['id']}"

    async def review(**body: Any) -> Any:
        return await client.post(f"{url}/review", json=body, headers=world.ada)

    assert (await review(action="acknowledge")).json()["status"] == "acknowledged"
    no_reason = await review(action="dismiss")
    assert (no_reason.status_code, no_reason.json()["error"]["details"]["reason"]) == (409, "note_required")
    detected = await review(action="detected")
    assert detected.json()["error"]["details"]["reason"] == "not_a_review_action"
    still = await review(action="resolve", evidenceAnalysisId=again["id"])
    assert still.json()["error"]["details"]["reason"] == "still_detected"
    nowhere = await review(action="link", link={"kind": "decision", "target": str(uuid.uuid4())})
    assert nowhere.json()["error"]["details"]["reason"] == "link_target_not_found"
    noted = await review(action="note", note="Cache added for the checkout spike.")
    assert noted.json()["history"][-1]["note"] == "Cache added for the checkout spike."
    linked = await review(action="link", link={"kind": "revision", "target": "1", "architectureId": aid})
    assert linked.json()["links"] == [{"kind": "revision", "target": "1", "architectureId": aid}]

    reverted = await analyze(client, world, aid, baseline_run)  # the sources no longer declare it
    resolved = await review(action="resolve", evidenceAnalysisId=reverted["id"])
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"
    reopened = await review(action="reopen", note="Seen again in staging.")
    assert reopened.json()["status"] == "reopened"
    assert (await client.get(url, headers=world.ada)).json() == reopened.json()
    assert await items(client, world, status="reopened") == [reopened.json()]

    assert await revisions(db, aid) == before  # reviewing never writes an architecture revision
    audit = await client.get(f"/api/v1/organizations/{world.org_id}/audit-log", headers=world.ada)
    actions = [e["action"] for e in audit.json()["entries"]]
    assert actions.count("drift_item.reviewed") == 5  # the refused actions record nothing
    assert "Cache added" not in str(audit.json())  # notes are not copied into the audit log


async def test_identity_mappings_are_confirmed_by_people(client: AsyncClient, world: World) -> None:
    aid, _ = await accepted(client, world)
    url = f"{project(world)}/architectures/{aid}/identity-mappings"
    unknown = await client.post(url, json={"baselineId": "nope", "discoveredKey": CACHE}, headers=world.ada)
    assert (unknown.status_code, unknown.json()["error"]["details"]["reason"]) == (422, "not_a_node")
    confirmed = await client.post(
        url, json={"baselineId": DB, "discoveredKey": CACHE, "note": "Renamed."}, headers=world.ada
    )
    assert confirmed.status_code == 201, confirmed.text
    retracted = await client.post(url, json={"baselineId": DB, "discoveredKey": None}, headers=world.ada)
    assert retracted.status_code == 201, retracted.text
    listed = (await client.get(url, headers=world.ada)).json()["mappings"]
    assert [m["discoveredKey"] for m in listed] == [CACHE, None]  # history: the latest applies


async def test_authorization_and_tenant_isolation(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    aid, run_id = await accepted(client, world)
    analysis = await analyze(client, world, aid, await discover(client, world, V2))
    item = (await items(client, world))[0]
    viewer = await member(client, db, outbox, world, "viewer")
    reads = (
        f"{project(world)}/drift-analyses",
        f"{project(world)}/drift-analyses/{analysis['id']}",
        f"{project(world)}/drift-analyses/{analysis['id']}/findings",
        f"{project(world)}/drift-items",
        f"{project(world)}/drift-items/{item['id']}",
        f"{project(world)}/architectures/{aid}/identity-mappings",
    )
    for path in reads:
        assert (await client.get(path, headers=viewer)).status_code == 200, path
    writes: list[tuple[str, dict[str, Any]]] = [
        (
            f"{project(world)}/drift-analyses",
            {"architectureId": aid, "baselineRevision": 1, "discoveryRunId": run_id},
        ),
        (f"{project(world)}/drift-items/{item['id']}/review", {"action": "acknowledge"}),
        (f"{project(world)}/architectures/{aid}/identity-mappings", {"baselineId": DB, "discoveredKey": DB}),
    ]
    for path, body in writes:
        denied = await client.post(path, json=body, headers=viewer)
        assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")

    stranger = await signed_in(client, outbox, "eve@example.com")
    for path in reads:
        assert (await client.get(path, headers=stranger)).json()["error"]["code"] == "project_not_found"
    assert (await client.get(reads[0])).status_code == 401

    other = project(world, world.other_id)
    hidden = await client.get(f"{other}/drift-analyses/{analysis['id']}", headers=world.ada)
    assert hidden.json()["error"]["code"] == "drift_analysis_not_found"
    hidden_item = await client.get(f"{other}/drift-items/{item['id']}", headers=world.ada)
    assert hidden_item.json()["error"]["code"] == "drift_item_not_found"
    cross = await client.post(
        f"{other}/drift-analyses",
        json={"architectureId": aid, "baselineRevision": 1, "discoveryRunId": run_id},
        headers=world.ada,
    )
    assert cross.json()["error"]["code"] == "architecture_not_found"  # not of that project
    foreign_evidence = await client.post(
        f"{project(world)}/drift-items/{item['id']}/review",
        json={"action": "resolve", "evidenceAnalysisId": str(uuid.uuid4())},
        headers=world.ada,
    )
    assert foreign_evidence.json()["error"]["code"] == "drift_analysis_not_found"


async def test_invalid_requests_store_nothing(client: AsyncClient, db: AsyncSession, world: World) -> None:
    aid, run_id = await accepted(client, world)
    url = f"{project(world)}/drift-analyses"
    base = {"architectureId": aid, "baselineRevision": 1, "discoveryRunId": run_id}
    cases: list[tuple[dict[str, Any], int, str]] = [
        (base | {"policy": "strict"}, 422, "invalid_drift_request"),
        (base | {"baselineRevision": 9}, 404, "architecture_revision_not_found"),
        (base | {"discoveryRunId": str(uuid.uuid4())}, 404, "discovery_run_not_found"),
        (base | {"exclude": ["../etc"]}, 422, "invalid_drift_request"),
        (base | {"score": 3}, 422, "validation_error"),
    ]
    for body, status_code, code in cases:
        response = await client.post(url, json=body, headers=world.ada)
        assert (response.status_code, response.json()["error"]["code"]) == (status_code, code), body
    assert await db.scalar(select(func.count()).select_from(DriftAnalysisRecord)) == 0


async def test_stored_records_are_append_only_and_a_compared_run_is_kept(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid, _ = await accepted(client, world)
    run_id = await discover(client, world, V2)
    analysis = await analyze(client, world, aid, run_id)
    item = (await items(client, world))[0]
    await client.post(
        f"{project(world)}/architectures/{aid}/identity-mappings",
        json={"baselineId": DB, "discoveredKey": DB},
        headers=world.ada,
    )
    for statement, message in (
        ("UPDATE drift_analyses SET status = 'failed' WHERE id = :id", "append-only"),
        ("DELETE FROM drift_analyses WHERE id = :id", "append-only"),
        ("TRUNCATE drift_analyses CASCADE", "append-only"),
        ("UPDATE drift_items SET subject = 'node:x' WHERE id = :item", "identity of an item never changes"),
        ("UPDATE drift_items SET history = '[]'::jsonb WHERE id = :item", "only grows"),
        ("DELETE FROM drift_items WHERE id = :item", "is kept"),
        ("UPDATE drift_identity_mappings SET note = 'x'", "append-only"),
        ("DELETE FROM drift_identity_mappings", "append-only"),
    ):
        with pytest.raises(DBAPIError, match=message):
            async with db.begin_nested():
                await db.execute(text(statement), {"id": analysis["id"], "item": item["id"]})
    kept = await client.delete(f"{project(world)}/discovery-runs/{run_id}", headers=world.ada)
    assert (kept.status_code, kept.json()["error"]["code"]) == (409, "discovery_run_in_use")
