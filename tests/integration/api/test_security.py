"""The security API against a real database (Milestone 10, phase 9)."""

import json
import logging
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import String, cast, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from apps.api.email.transport import InMemoryTransport
from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import to_dict
from persistence.models import (
    AuditLogRecord,
    SecurityAnalysisRecord,
    SecurityComponentRecord,
    SecurityFindingRecord,
)
from tests.unit.architecture_ir.builders import connection, node

from .requirement_support import World, member, signed_in

pytestmark = pytest.mark.integration

SECRET = "sk_live_do_not_leak"
API: dict[str, Any] = {
    "exposure": "public",
    "authentication": "oauth2",
    "data_classification": "internal",
    "secrets_required": True,
    "secret_source": "secret_manager",
}
IR = ArchitectureIR(
    "Shop",
    nodes=(
        node("web", NodeKind.CLIENT),
        node(
            "zone",
            NodeKind.BOUNDARY,
            configuration=Configuration({"boundary_type": "trust_zone", "trust_level": "internal"}),
        ),
        node("api", configuration=Configuration(API, extra={"stripe_api_key": SECRET})),
        node("admin", configuration=Configuration({"exposure": "public", "management_interface": True})),
        node(
            "db",
            NodeKind.DATABASE,
            parent_id="zone",
            configuration=Configuration({"exposure": "private", "personal_data": True}),
        ),
    ),
    connections=(
        connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https"),
        connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql"),
    ),
)


async def architecture(client: AsyncClient, world: World, ir: ArchitectureIR = IR) -> str:
    response = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures",
        json={"name": ir.name, "ir": to_dict(ir)},
        headers=world.ada,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def base(world: World, aid: str) -> str:
    return f"/api/v1/projects/{world.project_id}/architectures/{aid}/security-analyses"


async def run(client: AsyncClient, world: World, aid: str, **body: Any) -> dict[str, Any]:
    response = await client.post(base(world, aid), json=body, headers=world.ada)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def test_an_analysis_is_run_stored_and_read_back(
    client: AsyncClient, db: AsyncSession, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    aid = await architecture(client, world)
    with caplog.at_level(logging.DEBUG):
        created = await run(client, world, aid, label="Launch")
    assert (created["status"], created["revision"], created["label"]) == ("partial", 1, "Launch")
    assert created["trustZones"] == [{"boundaryId": "zone", "trustLevel": "internal", "nodeIds": ["db"]}]
    assert {x["code"] for x in created["limitations"]} >= {"architecture_level", "no_defaults"}
    assert "score" not in created["summary"]
    assert created["inputs"]["policy"]["require_encryption_at_rest"] is False
    assert (await client.get(f"{base(world, aid)}/{created['id']}", headers=world.ada)).json() == created

    url = f"{base(world, aid)}/{created['id']}"
    page = (await client.get(f"{url}/findings", params={"limit": 500}, headers=world.ada)).json()
    findings = page["findings"]
    types = {f["type"] for f in findings}
    assert {"public_management_interface", "encryption_not_modeled", "secret_in_configuration"} <= types
    management = next(f for f in findings if f["type"] == "public_management_interface")
    assert (management["basis"], management["category"], management["severity"]) == (
        "control_gap",
        "exposure",
        "high",
    )
    assert management["id"].startswith("sec_")
    leaked = next(f for f in findings if f["type"] == "secret_in_configuration")
    assert leaked["evidence"] == [{"label": "api.configuration.extra.stripe_api_key", "value": "[redacted]"}]
    components = (await client.get(f"{url}/components", headers=world.ada)).json()["components"]
    coverage = {c["nodeId"]: c["coverage"] for c in components}
    assert coverage == {"admin": "partial", "api": "modeled", "db": "partial"}

    analysis_id = uuid.UUID(created["id"])
    rows = select(func.count()).where(SecurityComponentRecord.analysis_id == analysis_id)
    assert await db.scalar(rows) == 3
    stored = select(func.count()).where(SecurityFindingRecord.analysis_id == analysis_id)
    assert await db.scalar(stored) == len(findings)
    audit = await db.scalar(
        select(AuditLogRecord).where(AuditLogRecord.action == "architecture.security_analyzed")
    )
    assert audit is not None
    assert audit.event_metadata["analysis_id"] == created["id"]
    persisted = (
        select(func.count())
        .select_from(SecurityFindingRecord)
        .where(cast(SecurityFindingRecord.data, String).contains(SECRET))
    )
    assert await db.scalar(persisted) == 0  # never persisted
    assert SECRET not in json.dumps([created, findings, components])
    assert SECRET not in caplog.text


async def test_findings_are_filtered_and_paged(client: AsyncClient, world: World) -> None:
    aid = await architecture(client, world)
    created = await run(client, world, aid)
    url = f"{base(world, aid)}/{created['id']}/findings"
    everything = (await client.get(url, params={"limit": 500}, headers=world.ada)).json()["findings"]
    first = (await client.get(url, params={"limit": 1}, headers=world.ada)).json()
    params = {"cursor": first["nextCursor"], "limit": 500}
    rest = (await client.get(url, params=params, headers=world.ada)).json()
    assert first["findings"] + rest["findings"] == everything
    for name, value in (
        ("basis", "control_gap"),
        ("category", "exposure"),
        ("threat", "elevation_of_privilege"),
    ):
        filtered = (await client.get(url, params={name: value}, headers=world.ada)).json()["findings"]
        assert filtered
        assert {f[name] for f in filtered} == {value}


async def test_missing_configuration_cannot_be_evaluated_rather_than_secure(
    client: AsyncClient, world: World
) -> None:
    thin = ArchitectureIR(
        "Thin",
        nodes=(node("web", NodeKind.CLIENT), node("api"), node("db", NodeKind.DATABASE)),
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="http"),
            connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql"),
        ),
    )
    aid = await architecture(client, world, thin)
    created = await run(client, world, aid)
    assert created["status"] == "insufficient_input"  # unknown is not secure
    url = f"{base(world, aid)}/{created['id']}/findings"
    findings = (await client.get(url, headers=world.ada)).json()["findings"]
    assert {f["basis"] for f in findings} == {"not_evaluable"}
    assert {f["type"] for f in findings} >= {"exposure_not_modeled", "data_classification_not_modeled"}


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"scope": ["ghost"]}, 422, "invalid_security_request"),
        ({"analyzers": ["scanner"]}, 422, "invalid_security_request"),
        ({"analyzers": ["threat model"]}, 422, "validation_error"),
        ({"revision": 9}, 404, "architecture_revision_not_found"),
        ({"status": "completed"}, 422, "validation_error"),
        ({"policy": {"require_tls": True}}, 422, "validation_error"),  # the policy is the project's
    ],
)
async def test_invalid_requests_are_refused_and_store_nothing(
    client: AsyncClient, db: AsyncSession, world: World, body: dict[str, Any], status: int, code: str
) -> None:
    aid = await architecture(client, world)
    response = await client.post(base(world, aid), json=body, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert await db.scalar(select(func.count()).select_from(SecurityAnalysisRecord)) == 0


async def test_access_and_listing(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    aid = await architecture(client, world)
    created = await run(client, world, aid)
    viewer = await member(client, db, outbox, world, "viewer")
    assert (await client.get(f"{base(world, aid)}/{created['id']}", headers=viewer)).status_code == 200
    denied = await client.post(base(world, aid), json={}, headers=viewer)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    stranger = await signed_in(client, outbox, "eve@example.com")
    for method, url in (("get", f"{base(world, aid)}/{created['id']}/findings"), ("post", base(world, aid))):
        response = await client.request(method, url, json={}, headers=stranger)
        assert response.json()["error"]["code"] == "project_not_found"
    other = f"/api/v1/projects/{world.other_id}/architectures/{aid}/security-analyses/{created['id']}"
    assert (await client.get(other, headers=world.ada)).json()["error"]["code"] == "architecture_not_found"
    missing = await client.get(f"{base(world, aid)}/{uuid.uuid4()}", headers=world.ada)
    assert missing.json()["error"]["code"] == "security_analysis_not_found"
    second = await run(client, world, aid)
    listed = (await client.get(base(world, aid), params={"limit": 1}, headers=world.ada)).json()
    params = {"cursor": listed["nextCursor"]}
    rest = (await client.get(base(world, aid), params=params, headers=world.ada)).json()
    assert [a["id"] for a in listed["analyses"] + rest["analyses"]] == [second["id"], created["id"]]


async def test_an_engine_failure_is_stored_as_failed(app: FastAPI, client: AsyncClient, world: World) -> None:
    aid = await architecture(client, world)

    class Broken:
        def analyze(self, *args: Any) -> Any:
            raise RuntimeError(f"internal detail {SECRET}")

        def analyzers(self) -> tuple[()]:
            return ()

    app.state.security_engine = Broken()
    created = await run(client, world, aid)
    assert (created["status"], created["error"]["code"], created["summary"]) == (
        "failed",
        "engine_error",
        None,
    )
    assert "internal" not in str(created)
    assert SECRET not in str(created)


async def test_the_analyzer_catalog(client: AsyncClient, world: World) -> None:
    analyzers = (await client.get("/api/v1/security/analyzers", headers=world.ada)).json()["analyzers"]
    threats = next(a for a in analyzers if a["id"] == "threat-model")
    assert threats["category"] == "threat"
    assert any("denial" in u.lower() for u in threats["unsupported"])
    assert (await client.get("/api/v1/security/analyzers")).status_code == 401


async def test_analyses_are_append_only_and_reads_cost_the_same(
    client: AsyncClient, db: AsyncSession, connection: AsyncConnection, world: World
) -> None:
    from .test_query_budget import counting  # noqa: PLC0415 - shared helper of the budget tests

    aid = await architecture(client, world)
    small = await run(client, world, aid, analyzers=["exposure"])
    large = await run(client, world, aid)
    for statement in (
        "UPDATE security_analyses SET label = 'x' WHERE id = :id",
        "DELETE FROM security_findings WHERE analysis_id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin_nested():
                await db.execute(text(statement), {"id": small["id"]})

    async def cost(analysis_id: str) -> list[int]:
        counts = []
        for suffix in ("", "/components", "/findings"):
            with counting(connection) as statements:
                url = f"{base(world, aid)}/{analysis_id}{suffix}"
                assert (await client.get(url, headers=world.ada)).status_code == 200
            counts.append(len(statements))
        return counts

    assert await cost(small["id"]) == await cost(large["id"])
