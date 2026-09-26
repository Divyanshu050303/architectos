import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import AuditLogRecord

from .requirement_support import (
    RPS,
    THROUGHPUT,
    WEB,
    World,
    create,
    member,
    signed_in,
)

pytestmark = pytest.mark.integration

# --- happy paths ---------------------------------------------------------------------------------


async def test_create_and_read(client: AsyncClient, world: World) -> None:
    created = await create(client, world)
    assert created == created | {
        "projectId": world.project_id,
        "reference": "REQ-1",
        "number": 1,
        "version": 1,
        "type": "capacity",
        "status": "active",
        "source": "user",
        "confidence": None,
        "structuredData": RPS | {"value": "2000"},  # exact decimals come back as strings
    }
    fetched = await client.get(f"{world.base}/{created['id']}", headers=world.ada)
    assert fetched.json() == created


async def test_ai_requirements_are_drafts_with_confidence(client: AsyncClient, world: World) -> None:
    created = await create(client, world, source="ai", status="draft", confidence="0.85")
    assert (created["source"], created["status"], created["confidence"]) == ("ai", "draft", "0.85")
    refused = await client.post(
        world.base, json=THROUGHPUT | {"source": "ai", "confidence": "0.85"}, headers=world.ada
    )
    assert refused.json()["error"]["details"] == {"field": "status", "reason": "not_allowed_at_creation"}


async def test_update_creates_versions_and_is_audited(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    created = await create(client, world)
    url = f"{world.base}/{created['id']}"

    updated = await client.patch(
        url,
        json={
            "expectedVersion": 1,
            "structuredData": RPS | {"value": "5000"},
            "statement": "Support 5,000 requests per second.",
            "changeReason": "Traffic forecast increased from 2K to 5K RPS",
        },
        headers=world.ada,
    )
    assert updated.status_code == 200, updated.text
    assert (updated.json()["version"], updated.json()["structuredData"]["value"]) == (2, "5000")

    satisfied = await client.patch(
        url,
        json={"expectedVersion": 2, "status": "satisfied", "changeReason": "Load test passed"},
        headers=world.ada,
    )
    assert (satisfied.json()["status"], satisfied.json()["version"]) == ("satisfied", 3)

    actions = list(
        await db.scalars(
            select(AuditLogRecord.action)
            .where(AuditLogRecord.resource_id == created["id"])
            .order_by(AuditLogRecord.created_at, AuditLogRecord.id)
        )
    )
    assert actions == [
        "requirement.created",
        "requirement.version_created",
        "requirement.updated",
        "requirement.version_created",
        "requirement.status_changed",
    ]


async def test_delete_is_soft(client: AsyncClient, world: World) -> None:
    created = await create(client, world)
    url = f"{world.base}/{created['id']}"
    assert (await client.delete(url, headers=world.ada)).status_code == 204
    assert (await client.get(url, headers=world.ada)).json()["error"]["code"] == "requirement_not_found"
    assert (await client.delete(url, headers=world.ada)).status_code == 404
    assert (await client.get(world.base, headers=world.ada)).json()["requirements"] == []
    assert (await create(client, world))["reference"] == "REQ-2"


# --- validation and conflicts --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field", "reason"),
    [
        ({"structuredData": RPS | {"value": -50}}, "structuredData.value", "out_of_range"),
        ({"structuredData": RPS | {"unit": "furlongs/fortnight"}}, "structuredData.unit", "unknown_unit"),
        (
            {
                "category": "storage",
                "structuredData": {"metric": "storage", "operator": ">=", "value": 5, "unit": "gb"},
            },
            "structuredData.unit",
            "ambiguous_unit",
        ),
        ({"structuredData": RPS | {"exec": "__import__('os')"}}, "structuredData.exec", "unknown_field"),
        ({"structuredData": {}}, "structuredData", "required_when_in_force"),
        ({"category": "vibes"}, "category", "unknown_for_type"),
        ({"title": "   "}, "title", "length"),
        ({"confidence": "0.9"}, "confidence", "not_for_user"),
        (
            {
                "type": "availability",
                "category": "availability",
                "structuredData": {"metric": "availability", "operator": ">=", "value": 150, "unit": "%"},
            },
            "structuredData.value",
            "out_of_range",
        ),
    ],
    ids=[
        "negative-rps",
        "unknown-unit",
        "ambiguous-unit",
        "code-in-data",
        "missing-constraint",
        "unknown-category",
        "blank-title",
        "user-confidence",
        "availability-150",
    ],
)
async def test_invalid_requirements(
    client: AsyncClient, world: World, overrides: dict[str, Any], field: str, reason: str
) -> None:
    response = await client.post(world.base, json=THROUGHPUT | overrides, headers=world.ada)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert (error["code"], error["details"]) == ("invalid_requirement", {"field": field, "reason": reason})


@pytest.mark.parametrize(
    "body",
    [
        THROUGHPUT | {"type": "vibes"},
        THROUGHPUT | {"priority": "urgent"},
        THROUGHPUT | {"projectId": str(uuid.uuid4())},
        THROUGHPUT | {"number": 7},
        THROUGHPUT | {"structuredData": [1, 2]},
        {k: v for k, v in THROUGHPUT.items() if k != "title"},
    ],
    ids=["type", "priority", "project-id", "number", "data-array", "missing-title"],
)
async def test_malformed_bodies(client: AsyncClient, world: World, body: dict[str, Any]) -> None:
    response = await client.post(world.base, json=body, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "validation_error")


async def test_stale_updates_conflict(client: AsyncClient, world: World) -> None:
    created = await create(client, world, status="draft")
    url = f"{world.base}/{created['id']}"
    await client.patch(url, json={"expectedVersion": 1, "title": "Mine"}, headers=world.ada)
    stale = await client.patch(url, json={"expectedVersion": 1, "title": "Theirs"}, headers=world.ada)
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert (error["code"], error["details"]) == ("requirement_version_conflict", {"currentVersion": 2})


async def test_lifecycle_errors(client: AsyncClient, world: World) -> None:
    created = await create(client, world)
    url = f"{world.base}/{created['id']}"

    no_reason = await client.patch(url, json={"expectedVersion": 1, "title": "Renamed"}, headers=world.ada)
    assert (no_reason.status_code, no_reason.json()["error"]["code"]) == (422, "change_reason_required")

    jump = await client.patch(
        url, json={"expectedVersion": 1, "status": "draft", "changeReason": "x"}, headers=world.ada
    )
    assert (jump.status_code, jump.json()["error"]["code"]) == (409, "invalid_status_transition")
    assert jump.json()["error"]["details"] == {"from": "active", "to": "draft"}

    await client.patch(
        url,
        json={"expectedVersion": 1, "status": "deprecated", "changeReason": "Out of scope"},
        headers=world.ada,
    )
    locked = await client.patch(
        url, json={"expectedVersion": 2, "title": "Back", "changeReason": "x"}, headers=world.ada
    )
    assert (locked.status_code, locked.json()["error"]["code"]) == (409, "requirement_locked")

    empty = await client.patch(url, json={"expectedVersion": 2}, headers=world.ada)
    assert empty.json()["error"]["code"] == "nothing_to_update"


async def test_archived_projects_are_read_only(client: AsyncClient, world: World) -> None:
    created = await create(client, world)
    url = f"{world.base}/{created['id']}"
    await client.post(f"/api/v1/projects/{world.project_id}/archive", headers=world.ada)

    attempts = [
        await client.post(world.base, json=THROUGHPUT, headers=world.ada),
        await client.patch(
            url, json={"expectedVersion": 1, "title": "x", "changeReason": "x"}, headers=world.ada
        ),
        await client.delete(url, headers=world.ada),
    ]
    assert [(r.status_code, r.json()["error"]["code"]) for r in attempts] == [(409, "project_archived")] * 3
    assert (await client.get(url, headers=world.ada)).status_code == 200


# --- authorization -------------------------------------------------------------------------------


async def test_a_requirement_is_only_reachable_through_its_project(client: AsyncClient, world: World) -> None:
    created = await create(client, world)
    wrong = f"/api/v1/projects/{world.other_id}/requirements/{created['id']}"
    responses = [
        await client.get(wrong, headers=world.ada),
        await client.patch(
            wrong, json={"expectedVersion": 1, "title": "x", "changeReason": "x"}, headers=world.ada
        ),
        await client.delete(wrong, headers=world.ada),
    ]
    assert [(r.status_code, r.json()["error"]["code"]) for r in responses] == [
        (404, "requirement_not_found")
    ] * 3
    listed = (await client.get(f"/api/v1/projects/{world.other_id}/requirements", headers=world.ada)).json()
    assert listed["requirements"] == []


@pytest.mark.parametrize(("role", "can_write"), [("admin", True), ("member", True), ("viewer", False)])
async def test_roles(
    client: AsyncClient,
    db: AsyncSession,
    outbox: InMemoryTransport,
    world: World,
    role: str,
    can_write: bool,
) -> None:
    created = await create(client, world, status="draft")
    headers = await member(client, db, outbox, world, role)
    url = f"{world.base}/{created['id']}"

    reads = [
        (await client.get(world.base, headers=headers)).status_code,
        (await client.get(url, headers=headers)).status_code,
    ]
    writes = [
        (await client.post(world.base, json=THROUGHPUT, headers=headers)).status_code,
        (await client.patch(url, json={"expectedVersion": 1, "title": "x"}, headers=headers)).status_code,
        (await client.delete(url, headers=headers)).status_code,
    ]
    assert reads == [200, 200]
    assert writes == ([201, 200, 204] if can_write else [403, 403, 403])


async def test_removed_members_lose_access(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    created = await create(client, world)
    headers = await member(client, db, outbox, world, "member")
    url = f"{world.base}/{created['id']}"
    assert (await client.get(url, headers=headers)).status_code == 200

    members = (await client.get(f"/api/v1/organizations/{world.org_id}/members", headers=world.ada)).json()[
        "members"
    ]
    target = next(m for m in members if m["role"] == "member")
    await client.delete(
        f"/api/v1/organizations/{world.org_id}/members/{target['id']}", headers=world.ada | WEB
    )

    response = await client.get(url, headers=headers)
    assert (response.status_code, response.json()["error"]["code"]) == (404, "project_not_found")


# --- listing -------------------------------------------------------------------------------------


async def test_list_paginates_and_filters(client: AsyncClient, world: World) -> None:
    titles = [f"Throughput {i}" for i in range(5)]
    for title in titles:
        await create(client, world, title=title)
    await create(
        client,
        world,
        type="functional",
        category="order",
        title="Place an order",
        status="draft",
        priority="low",
        structuredData={},
    )

    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, str | int] = {"limit": 2, "type": "capacity"}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get(world.base, params=params, headers=world.ada)).json()
        seen += [r["title"] for r in page["requirements"]]
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert seen == list(reversed(titles))

    for key, value in (("status", "draft"), ("priority", "low"), ("category", "ORDER"), ("search", "an ord")):
        found = (await client.get(world.base, params={key: value}, headers=world.ada)).json()["requirements"]
        assert [r["title"] for r in found] == ["Place an order"], key

    bad = await client.get(world.base, params={"cursor": "garbage"}, headers=world.ada)
    assert bad.json()["error"]["code"] == "invalid_cursor"
    bad_type = await client.get(world.base, params={"type": "vibes"}, headers=world.ada)
    assert bad_type.status_code == 422


async def test_unknown_project_or_requirement(client: AsyncClient, world: World) -> None:
    missing_project = await client.get(f"/api/v1/projects/{uuid.uuid4()}/requirements", headers=world.ada)
    assert missing_project.json()["error"]["code"] == "project_not_found"
    missing = await client.get(f"{world.base}/{uuid.uuid4()}", headers=world.ada)
    assert missing.json()["error"]["code"] == "requirement_not_found"
    not_a_uuid = await client.get(f"{world.base}/REQ-1", headers=world.ada)
    assert not_a_uuid.status_code == 422


# --- normalization, validation and analysis ------------------------------------------------------


async def test_convenient_input_is_normalized_and_the_canonical_form_returned(
    client: AsyncClient, world: World
) -> None:
    created = await create(
        client,
        world,
        type="availability",
        category="availability",
        structuredData={"metric": "availability", "operator": ">=", "quantity": "99.9%"},
    )
    assert created["structuredData"] == {
        "metric": "availability",
        "operator": ">=",
        "value": "99.9",
        "unit": "%",
    }
    assert created["normalizedData"] == {
        "metric": "availability",
        "operator": ">=",
        "value": "0.999",
        "unit": "ratio",
    }

    rps = await create(
        client, world, structuredData={"metric": "rps", "operator": ">=", "value": "2k", "unit": "req/s"}
    )
    assert rps["structuredData"] == RPS | {"value": "2000"}


async def test_validate_reports_without_changing_anything(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    draft = await create(client, world, status="draft", structuredData={})
    viewer = await member(client, db, outbox, world, "viewer")

    response = await client.post(f"{world.base}/{draft['id']}/validate", headers=viewer)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "requirement": {"id": draft["id"], "reference": "REQ-1", "version": 1},
        "valid": True,
        "readyForActive": False,
        "issues": [
            {"severity": "warning", "field": "structuredData", "reason": "missing_constraint"},
            {"severity": "warning", "field": "structuredData", "reason": "not_ready_for_active"},
        ],
    }
    assert (await client.get(f"{world.base}/{draft['id']}", headers=world.ada)).json() == draft


async def test_analysis_finds_conflicts_and_gaps(client: AsyncClient, world: World) -> None:
    floor = await create(client, world, structuredData=RPS | {"value": 10_000})
    ceiling = await create(
        client, world, title="Cap", structuredData=RPS | {"operator": "<=", "value": 5_000}
    )
    await create(
        client, world, title="Retired", status="draft", structuredData=RPS | {"operator": "<=", "value": 1}
    )
    retired = (await client.get(world.base, params={"search": "Retired"}, headers=world.ada)).json()[
        "requirements"
    ][0]
    await client.patch(
        f"{world.base}/{retired['id']}",
        json={"expectedVersion": 1, "status": "deprecated"},
        headers=world.ada,
    )

    response = await client.get(f"{world.base}/analysis", headers=world.ada)
    assert response.status_code == 200, response.text
    analysis = response.json()

    def ref(requirement: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": requirement["id"],
            "reference": requirement["reference"],
            "version": requirement["version"],
        }

    assert analysis["requirements"] == [ref(floor), ref(ceiling)]
    assert analysis["truncated"] is False
    assert analysis["conflicts"] == [
        {
            "reason": "disjoint_bounds",
            "metric": "requests_per_second",
            "requirements": [ref(floor), ref(ceiling)],
            "message": "REQ-1 requires requests_per_second >= 10000 requests/second, but REQ-2 requires "
            "requests_per_second <= 5000 requests/second: no value satisfies both.",
        }
    ]
    assert analysis["completeness"] == {
        "covered": [{"concern": "traffic", "requirements": [ref(floor), ref(ceiling)]}],
        "missing": ["latency", "availability", "data", "security", "retention"],
    }
    assert (analysis["ambiguous"], analysis["unbounded"]) == ([], [])


async def test_analysis_of_an_empty_project(client: AsyncClient, world: World) -> None:
    analysis = (await client.get(f"{world.base}/analysis", headers=world.ada)).json()
    assert analysis["requirements"] == []
    assert len(analysis["completeness"]["missing"]) == 6


async def test_analysis_and_validation_are_tenant_scoped(
    client: AsyncClient, outbox: InMemoryTransport, world: World
) -> None:
    created = await create(client, world)
    stranger = await signed_in(client, outbox, "eve@example.com")
    for response in (
        await client.get(f"{world.base}/analysis", headers=stranger),
        await client.post(f"{world.base}/{created['id']}/validate", headers=stranger),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (404, "project_not_found")
    wrong = await client.post(
        f"/api/v1/projects/{world.other_id}/requirements/{created['id']}/validate", headers=world.ada
    )
    assert wrong.json()["error"]["code"] == "requirement_not_found"


# --- scope, targets, ranges and sources (Requirements Engine phase 1) -----------------------------


async def test_scope_targets_and_ranges_round_trip(client: AsyncClient, world: World) -> None:
    created = await create(
        client,
        world,
        type="performance",
        category="latency",
        scope="api",
        structuredData={
            "metric": "latency",
            "operator": "=",
            "value": "300",
            "unit": "ms",
            "percentile": "p95",
        },
    )
    assert (created["scope"], created["structuredData"]["operator"]) == ("api", "==")
    storage = await create(
        client,
        world,
        type="data",
        category="data_volume",
        structuredData={"metric": "storage", "operator": "range", "min": "10", "max": "20", "unit": "GB"},
    )
    assert storage["scope"] == "system"
    assert storage["normalizedData"] == {
        "metric": "storage",
        "operator": "between",
        "min": "10000000000",
        "max": "20000000000",
        "unit": "B",
    }
    changed = await client.patch(
        f"{world.base}/{created['id']}",
        json={"expectedVersion": 1, "scope": "database", "changeReason": "It is the database"},
        headers=world.ada,
    )
    assert (changed.json()["scope"], changed.json()["version"]) == ("database", 2)
    history = await client.get(f"{world.base}/{created['id']}/versions", headers=world.ada)
    assert [v["scope"] for v in history.json()["versions"]] == ["api", "database"]


async def test_ranges_conflict_through_the_analysis(client: AsyncClient, world: World) -> None:
    await create(
        client,
        world,
        structuredData={"metric": "rps", "operator": "between", "min": 100, "max": 200, "unit": "rps"},
    )
    await create(client, world, structuredData=RPS | {"operator": ">=", "value": 500})
    analysis = (await client.get(f"{world.base}/analysis", headers=world.ada)).json()
    [conflict] = analysis["conflicts"]
    assert "between 100 and 200 requests/second" in conflict["message"]


async def test_other_sources_start_as_drafts(client: AsyncClient, world: World) -> None:
    imported = await create(client, world, source="imported", status="draft")
    assert (imported["source"], imported["confidence"]) == ("imported", None)
    refused = await client.post(world.base, json=THROUGHPUT | {"source": "discovery"}, headers=world.ada)
    assert refused.json()["error"]["details"] == {"field": "status", "reason": "not_allowed_at_creation"}
    unknown_scope = await client.post(world.base, json=THROUGHPUT | {"scope": "planet"}, headers=world.ada)
    assert unknown_scope.json()["error"]["code"] == "validation_error"
