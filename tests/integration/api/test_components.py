"""The component catalog API (ARCH-COMP-001, phase 6): categories, entries, exact specification
versions with every claim's provenance, and configuration evaluation — read-only reference data for
signed-in users, the configuration read by the Architecture IR's own rules, nothing stored."""

from typing import Any

import pytest
from httpx import AsyncClient

from .requirement_support import World

pytestmark = pytest.mark.integration

BASE = "/api/v1/components"


async def get(client: AsyncClient, world: World, path: str, status: int = 200) -> Any:
    response = await client.get(f"{BASE}{path}", headers=world.ada)
    assert response.status_code == status, response.text
    return response.json()


async def evaluate(
    client: AsyncClient, world: World, path: str, body: dict[str, Any], status: int = 200
) -> Any:
    response = await client.post(f"{BASE}{path}/evaluate", json=body, headers=world.ada)
    assert response.status_code == status, response.text
    return response.json()


def queue(**values: Any) -> dict[str, Any]:
    return {"nodeKind": "queue", "configuration": {"values": values}}


async def test_categories_and_entries_state_their_support(client: AsyncClient, world: World) -> None:
    categories = await get(client, world, "/categories")
    by_id = {c["id"]: c for c in categories["categories"]}
    assert set(by_id) == {"compute", "database", "messaging", "storage", "networking", "observability"}
    assert by_id["messaging"]["nodeKinds"] == ["queue"]
    assert by_id["database"]["byStatus"]["supported"] == 2  # PostgreSQL and Redis
    entries = await get(client, world, "")
    assert len(entries["components"]) == 39
    assert entries["catalogFingerprint"] == categories["catalogFingerprint"]
    supported = await get(client, world, "?status=supported")
    assert {c["id"] for c in supported["components"]} == {
        "databases/postgresql",
        "databases/redis",
        "messaging/kafka",
        "messaging/aws-sqs",
        "compute/aws-lambda",
    }
    messaging = await get(client, world, "?category=messaging")
    assert all(c["category"] == "messaging" for c in messaging["components"])
    await get(client, world, "?status=excellent", status=422)


async def test_a_specification_shows_every_claim_with_its_provenance(
    client: AsyncClient, world: World
) -> None:
    sqs = await get(client, world, "/messaging/aws-sqs")
    assert (sqs["ref"], sqs["supportStatus"], sqs["current"]) == ("messaging/aws-sqs@2", "supported", True)
    [retention] = sqs["constraints"]
    assert (retention["type"], retention["minimum"], retention["maximum"]) == ("hard_limit", "60", "1209600")
    assert retention["provenance"]["kind"] == "documented"
    sources = {s["id"]: s for s in sqs["sources"]}
    assert set(retention["provenance"]["sources"]) <= set(sources)
    assert sources["quotas"]["retrieved"] == "2026-09-28"
    assert sqs["billing"] == []  # no billing dimension verified: nothing stated, and never a price
    first = await get(client, world, "/messaging/aws-sqs?version=1")
    assert (first["ref"], first["supportStatus"], first["current"]) == (
        "messaging/aws-sqs@1",
        "planned",
        False,
    )
    versions = await get(client, world, "/messaging/aws-sqs/versions")
    assert [v["version"] for v in versions["versions"]] == [1, 2]
    assert versions["current"] == 2


async def test_unknown_components_and_versions_are_not_found(client: AsyncClient, world: World) -> None:
    for path in ("/databases/quantum-db", "/messaging/aws-sqs?version=9", "/databases/quantum-db/versions"):
        body = await get(client, world, path, status=404)
        assert body["error"]["code"] == "component_not_found"
    await get(client, world, "/Databases/postgresql", status=422)  # not a catalog path segment


async def test_a_configuration_is_evaluated_against_the_documented_constraints(
    client: AsyncClient, world: World
) -> None:
    violated = await evaluate(client, world, "/messaging/aws-sqs", queue(retention_seconds=30))
    [finding] = violated["findings"]
    assert (finding["outcome"], finding["severity"], finding["unit"]) == ("violation", "high", "s")
    assert finding["expected"] == "between 60 and 1209600"
    spec = await get(client, world, "/messaging/aws-sqs")
    assert violated["specifications"] == {"messaging/aws-sqs@2": spec["contentHash"]}
    passing = await evaluate(client, world, "/messaging/aws-sqs", queue(retention_seconds=3600))
    assert passing["summary"]["pass"] == 1
    unstated = await evaluate(client, world, "/messaging/aws-sqs", {"nodeKind": "queue"})
    assert unstated["summary"]["cannot_evaluate"] == 1  # unknown is never a pass
    assert unstated["summary"]["pass"] == 0
    planned = await evaluate(
        client, world, "/messaging/aws-sqs", queue(retention_seconds=30) | {"version": 1}
    )
    assert [f["check"] for f in planned["findings"]] == ["specification_planned"]
    assert await evaluate(client, world, "/messaging/aws-sqs", queue(retention_seconds=30)) == violated


async def test_the_configuration_is_read_by_the_ir_rules(client: AsyncClient, world: World) -> None:
    wrong_kind = await evaluate(client, world, "/messaging/aws-sqs", queue(eviction_policy="noeviction"), 422)
    assert wrong_kind["error"]["code"] == "invalid_architecture"
    invented = await evaluate(client, world, "/messaging/aws-sqs", queue(retention_seconds="forever"), 422)
    assert invented["error"]["code"] == "invalid_architecture"
    await evaluate(client, world, "/messaging/aws-sqs", {"nodeKind": "spaceship"}, 422)
    missing = await evaluate(client, world, "/databases/quantum-db", {"nodeKind": "database"}, 404)
    assert missing["error"]["code"] == "component_not_found"


async def test_the_catalog_needs_a_session(client: AsyncClient) -> None:
    for response in (
        await client.get(f"{BASE}/categories"),
        await client.get(f"{BASE}/messaging/aws-sqs"),
        await client.post(f"{BASE}/messaging/aws-sqs/evaluate", json={"nodeKind": "queue"}),
    ):
        assert response.status_code == 401
