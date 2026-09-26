"""Pricing snapshots over HTTP against a real database (Milestone 8, phase 2)."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import AuditLogRecord

from .requirement_support import World, member, signed_in

pytestmark = pytest.mark.integration


def price(record_id: str = "rds-euw1", **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": record_id,
        "provider": "aws",
        "service": "rds",
        "sku": "db.r6g.large",
        "region": "eu-west-1",
        "currency": "USD",
        "unit": "instance_hour",
        "model": "per_unit",
        "unitPrice": "0.26",
        "effectiveFrom": "2026-09-01",
        "source": "provider_import",
        "retrievedAt": "2026-09-20T08:00:00Z",
        "conditions": ["on_demand"],
    }
    return fields | overrides


TIERED = price(
    "s3-standard",
    service="s3",
    sku="TimedStorage-ByteHrs",
    unit="gb_month",
    model="tiered",
    unitPrice=None,
    tiers=[{"upTo": 50000, "unitPrice": "0.023"}, {"upTo": None, "unitPrice": "0.022"}],
)


def base(world: World) -> str:
    return f"/api/v1/organizations/{world.org_id}/pricing-snapshots"


async def create(
    client: AsyncClient, world: World, *records: dict[str, Any], name: str = "EU prices"
) -> dict[str, Any]:
    response = await client.post(
        base(world), json={"name": name, "records": list(records)}, headers=world.ada
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def test_a_snapshot_is_created_read_and_audited(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    created = await create(client, world, price(), TIERED, price("rds-use1", region="us-east-1"))
    assert (created["recordCount"], len(created["contentHash"])) == (3, 64)
    assert (await client.get(f"{base(world)}/{created['id']}", headers=world.ada)).json() == created
    records = (await client.get(f"{base(world)}/{created['id']}/records", headers=world.ada)).json()[
        "records"
    ]
    assert [r["id"] for r in records] == ["rds-euw1", "rds-use1", "s3-standard"]
    s3 = records[2]
    assert (s3["unitPrice"], s3["tiers"]) == (
        None,
        [{"upTo": "50000", "unitPrice": "0.023"}, {"upTo": None, "unitPrice": "0.022"}],
    )
    assert records[0]["retrievedAt"].startswith("2026-09-20T08:00:00")
    audit = await db.scalar(select(AuditLogRecord).where(AuditLogRecord.action == "pricing_snapshot.created"))
    assert audit is not None
    assert audit.event_metadata == {"records": 3, "content_sha256": created["contentHash"]}


async def test_the_same_prices_have_the_same_hash(client: AsyncClient, world: World) -> None:
    one = await create(client, world, price(), TIERED, name="One")
    other = await create(client, world, TIERED, price(), name="Other")
    assert one["contentHash"] == other["contentHash"]
    assert one["id"] != other["id"]


@pytest.mark.parametrize(
    ("record", "code", "field"),
    [
        (price(unitPrice="-1"), "invalid_money", "unitPrice"),
        (price(currency="usd"), "invalid_pricing_record", "currency"),
        (price(region="EU West"), "invalid_pricing_record", "region"),
        (price(unit="month"), "invalid_pricing_record", "unit"),
        (price(effectiveTo="2026-08-01"), "invalid_pricing_record", "effectiveTo"),
        (price(unit="gigabyte"), "validation_error", None),
        (price(discount="10%"), "validation_error", None),
        (price(retrievedAt="2026-09-20T08:00:00"), "validation_error", None),  # no timezone
    ],
)
async def test_invalid_records_are_refused(
    client: AsyncClient, world: World, record: dict[str, Any], code: str, field: str | None
) -> None:
    response = await client.post(base(world), json={"name": "Bad", "records": [record]}, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (422, code)
    if field is not None:
        assert response.json()["error"]["details"]["field"] == field


async def test_duplicate_record_ids_are_refused(client: AsyncClient, world: World) -> None:
    response = await client.post(
        base(world), json={"name": "Twice", "records": [price(), price()]}, headers=world.ada
    )
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid_pricing_snapshot")


async def test_members_read_only_admins_create_strangers_see_nothing(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    created = await create(client, world, price())
    contributor = await member(client, db, outbox, world, "member")
    assert (await client.get(f"{base(world)}/{created['id']}", headers=contributor)).status_code == 200
    denied = await client.post(base(world), json={"name": "Mine", "records": [price()]}, headers=contributor)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    grace = await signed_in(client, outbox, "grace@example.com")
    other = (await client.post("/api/v1/organizations", json={"name": "Globex"}, headers=grace)).json()["id"]
    theirs = f"/api/v1/organizations/{other}/pricing-snapshots/{created['id']}"
    assert (await client.get(theirs, headers=grace)).json()["error"]["code"] == "pricing_snapshot_not_found"
    assert (await client.get(f"{base(world)}/{created['id']}", headers=grace)).json()["error"][
        "code"
    ] == "organization_not_found"


async def test_listing_and_paging(client: AsyncClient, world: World) -> None:
    ids = [(await create(client, world, price(), name=f"List {i}"))["id"] for i in range(3)]
    page = (await client.get(base(world), params={"limit": 2}, headers=world.ada)).json()
    rest = (await client.get(base(world), params={"cursor": page["nextCursor"]}, headers=world.ada)).json()
    assert [s["id"] for s in page["snapshots"] + rest["snapshots"]] == ids[::-1]
    many = await create(
        client, world, *(price(f"r{i:04d}", sku=f"sku-{i}") for i in range(1200)), name="Many"
    )
    first = (
        await client.get(f"{base(world)}/{many['id']}/records", params={"limit": 500}, headers=world.ada)
    ).json()
    assert (len(first["records"]), first["records"][0]["id"]) == (500, "r0000")
    filtered = (
        await client.get(
            f"{base(world)}/{many['id']}/records", params={"region": "us-east-1"}, headers=world.ada
        )
    ).json()
    assert filtered["records"] == []


async def test_snapshots_are_append_only(client: AsyncClient, db: AsyncSession, world: World) -> None:
    created = await create(client, world, price())
    for statement in (
        "UPDATE pricing_snapshots SET name = 'x' WHERE id = :id",
        "UPDATE pricing_records SET data = '{}'::jsonb WHERE snapshot_id = :id",
        "DELETE FROM pricing_records WHERE snapshot_id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin_nested():
                await db.execute(text(statement), {"id": created["id"]})
