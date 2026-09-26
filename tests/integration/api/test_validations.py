"""The validation API against a real database (Milestone 6, phase 6)."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from core.architecture_ir.serialization import to_dict
from persistence.models import AuditLogRecord, ValidationFindingRecord, ValidationRunRecord
from tests.unit.architecture_ir.builders import api_and_postgres

from .requirement_support import World, member

pytestmark = pytest.mark.integration

IR = to_dict(api_and_postgres())
for _connection in IR["connections"]:
    if _connection["id"] == "api-db":
        _connection["configuration"] = {"values": {"tls": False}}


async def architecture(client: AsyncClient, world: World, ir: dict[str, Any] | None = None) -> str:
    response = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures",
        json={"name": "Orders", "ir": ir or IR},
        headers=world.ada,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def runs(world: World, architecture_id: str) -> str:
    return f"/api/v1/projects/{world.project_id}/architectures/{architecture_id}/validations"


async def run(client: AsyncClient, world: World, architecture_id: str, **body: Any) -> dict[str, Any]:
    response = await client.post(runs(world, architecture_id), json=body, headers=world.ada)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def strict_policy(client: AsyncClient, world: World) -> None:
    response = await client.put(
        f"/api/v1/projects/{world.project_id}/architecture-policy",
        json={"requireTls": True, "prohibitedTechnologies": ["fastapi"]},
        headers=world.ada,
    )
    assert response.status_code == 200, response.text


async def test_a_validation_is_run_stored_and_read_back(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    await strict_policy(client, world)
    aid = await architecture(client, world)

    created = await run(client, world, aid)

    assert (created["status"], created["revision"], created["profile"]) == ("completed", 1, "default")
    assert created["summary"]["blocking"] == 2  # fastapi is prohibited, api-db has tls off
    assert created["summary"]["bySeverity"]["high"] >= 2
    assert created["inputs"]["policy"]["require_tls"] is True
    assert [x["code"] for x in created["limitations"]] == ["catalog_unavailable"]
    assert created["ruleSet"]["id"] == "default"
    assert (await client.get(f"{runs(world, aid)}/{created['id']}", headers=world.ada)).json() == created

    stored = await db.scalar(
        select(func.count()).where(ValidationFindingRecord.run_id == uuid.UUID(created["id"]))
    )
    assert stored == created["summary"]["total"]
    audit = await db.scalar(select(AuditLogRecord).where(AuditLogRecord.action == "architecture.validated"))
    assert audit is not None
    assert audit.event_metadata["run_id"] == created["id"]


async def test_findings_are_paged_filtered_and_stable_across_runs(client: AsyncClient, world: World) -> None:
    await strict_policy(client, world)
    aid = await architecture(client, world)
    first, second = await run(client, world, aid), await run(client, world, aid)
    assert first["resultFingerprint"] == second["resultFingerprint"]

    def findings(run_id: str) -> str:
        return f"{runs(world, aid)}/{run_id}/findings"

    everything = (await client.get(findings(first["id"]), headers=world.ada)).json()["findings"]
    again = (await client.get(findings(second["id"]), headers=world.ada)).json()["findings"]
    assert [f["id"] for f in everything] == [f["id"] for f in again]  # stable finding ids

    page = (await client.get(findings(first["id"]), params={"limit": 1}, headers=world.ada)).json()
    rest = (
        await client.get(
            findings(first["id"]), params={"cursor": page["nextCursor"], "limit": 500}, headers=world.ada
        )
    ).json()
    assert [f["id"] for f in page["findings"] + rest["findings"]] == [f["id"] for f in everything]

    blocking = (
        await client.get(findings(first["id"]), params={"blocking": "true"}, headers=world.ada)
    ).json()
    assert {f["code"] for f in blocking["findings"]} == {"prohibited_technology", "tls_disabled"}
    by_rule = (
        await client.get(findings(first["id"]), params={"ruleId": "policy.tls"}, headers=world.ada)
    ).json()
    assert [f["entityIds"] for f in by_rule["findings"]] == [["api-db"]]
    by_entity = (
        await client.get(findings(first["id"]), params={"entityId": "api"}, headers=world.ada)
    ).json()
    assert all("api" in f["entityIds"] for f in by_entity["findings"])
    assert by_entity["findings"]


async def test_runs_are_listed_newest_first(client: AsyncClient, world: World) -> None:
    aid = await architecture(client, world)
    ids = [(await run(client, world, aid))["id"] for _ in range(3)]
    listed = (await client.get(runs(world, aid), params={"limit": 2}, headers=world.ada)).json()
    rest = (
        await client.get(runs(world, aid), params={"cursor": listed["nextCursor"]}, headers=world.ada)
    ).json()
    assert [r["id"] for r in listed["runs"] + rest["runs"]] == ids[::-1]
    assert rest["nextCursor"] is None
    only = (await client.get(runs(world, aid), params={"revision": 2}, headers=world.ada)).json()
    assert only["runs"] == []


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"profile": "paranoid"}, 422, "invalid_validation_config"),
        ({"rules": ["no.such-rule"]}, 422, "invalid_validation_config"),
        (
            {"parameters": {"structure.synchronous-cycle": {"include_unstated": 3}}},
            422,
            "invalid_validation_config",
        ),
        ({"severityOverrides": {"policy.tls": "info"}}, 422, "invalid_validation_config"),
        ({"severityOverrides": {"structure.empty-boundary": "urgent"}}, 422, "validation_error"),
        ({"revision": 7}, 404, "architecture_revision_not_found"),
        ({"status": "completed"}, 422, "validation_error"),
    ],
)
async def test_invalid_requests_are_refused_and_store_nothing(
    client: AsyncClient, db: AsyncSession, world: World, body: dict[str, Any], status: int, code: str
) -> None:
    aid = await architecture(client, world)
    response = await client.post(runs(world, aid), json=body, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert await db.scalar(select(func.count()).select_from(ValidationRunRecord)) == 0


async def test_rule_parameters_and_overrides_are_applied(client: AsyncClient, world: World) -> None:
    aid = await architecture(client, world)
    created = await run(
        client,
        world,
        aid,
        profile="strict",
        rules=["structure.synchronous-cycle"],
        parameters={"structure.synchronous-cycle": {"include_unstated": False}},
        severityOverrides={"structure.synchronous-cycle": "low"},
    )
    assert created["inputs"]["config"]["parameters"] == {
        "structure.synchronous-cycle": {"include_unstated": False}
    }
    ran = {rule_id for rule_id, _ in created["ruleSet"]["rules"]}
    assert "structure.synchronous-cycle" in ran
    assert "structure.disconnected-component" not in ran  # not selected
    assert {r for r in ran if r.startswith("policy.")} == {
        "policy.component-count",
        "policy.region",
        "policy.technology",
        "policy.tls",
    }  # mandatory


async def test_viewers_read_members_validate_strangers_get_404(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    aid = await architecture(client, world)
    created = await run(client, world, aid)
    viewer = await member(client, db, outbox, world, "viewer")
    contributor = await member(client, db, outbox, world, "member")

    assert (await client.get(f"{runs(world, aid)}/{created['id']}", headers=viewer)).status_code == 200
    denied = await client.post(runs(world, aid), json={}, headers=viewer)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    assert (await client.post(runs(world, aid), json={}, headers=contributor)).status_code == 201

    other = f"/api/v1/projects/{world.other_id}/architectures/{aid}/validations/{created['id']}"
    assert (await client.get(other, headers=world.ada)).json()["error"]["code"] == "architecture_not_found"
    missing = f"{runs(world, aid)}/{uuid.uuid4()}"
    assert (await client.get(missing, headers=world.ada)).json()["error"][
        "code"
    ] == "validation_run_not_found"


async def test_archived_architectures_are_not_validated(client: AsyncClient, world: World) -> None:
    aid = await architecture(client, world)
    await client.post(f"/api/v1/projects/{world.project_id}/architectures/{aid}/archive", headers=world.ada)
    refused = await client.post(runs(world, aid), json={}, headers=world.ada)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "architecture_archived")
    assert (await client.get(runs(world, aid), headers=world.ada)).status_code == 200  # still readable


async def test_the_rule_catalog(client: AsyncClient, world: World) -> None:
    catalog = (await client.get("/api/v1/validation/rules", headers=world.ada)).json()
    assert catalog["profiles"] == ["default", "strict"]
    tls = next(r for r in catalog["rules"] if r["id"] == "policy.tls")
    assert (tls["mandatory"], tls["category"], tls["inputs"]) == (True, "policy", ["policy"])
    assert (await client.get("/api/v1/validation/rules")).status_code == 401


async def test_runs_and_findings_are_append_only(client: AsyncClient, db: AsyncSession, world: World) -> None:
    await strict_policy(client, world)
    aid = await architecture(client, world)
    created = await run(client, world, aid)
    assert created["summary"]["total"] > 0  # row triggers need rows to act on
    for statement in (
        "UPDATE validation_runs SET profile = 'x' WHERE id = :id",
        "DELETE FROM validation_findings WHERE run_id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin_nested():
                await db.execute(text(statement), {"id": created["id"]})
