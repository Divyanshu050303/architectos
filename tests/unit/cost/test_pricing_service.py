"""Pricing snapshots of an organization (Milestone 8, phase 2): immutable, scoped, audited."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from core.domain.audit.entities import AuditAction
from core.domain.cost.errors import InvalidPricingSnapshot, PricingSnapshotNotFound
from core.domain.cost.pricing import PricingModel, PricingRecord, PricingSource, PricingUnit
from core.domain.cost.queries import RecordQuery
from core.domain.cost.snapshots import PricingService
from core.domain.organizations.errors import PermissionDenied
from core.domain.pagination import InvalidCursor
from tests.unit.architecture.world import World, make_world
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork


def record(record_id: str, region: str = "eu-west-1", price: str = "0.26") -> PricingRecord:
    return PricingRecord(
        record_id,
        "aws",
        "rds",
        "db.r6g.large",
        region,
        "USD",
        PricingUnit.INSTANCE_HOUR,
        PricingModel.PER_UNIT,
        date(2026, 9, 1),
        PricingSource.USER_INPUT,
        unit_price=Decimal(price),
        retrieved_at=datetime(2026, 9, 20, tzinfo=UTC),
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
async def world(uow: FakeUnitOfWork, clock: FakeClock) -> World:
    return await make_world(uow, clock)


async def owner(uow: FakeUnitOfWork, world: World):  # type: ignore[no-untyped-def]
    access = await uow.memberships.get_in_active_organization(
        organization_id=world.project.organization_id, user_id=world.ada.id
    )
    assert access is not None
    return access.membership


async def test_a_snapshot_is_created_immutable_and_audited(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    service = PricingService(uow, clock=clock)
    membership = await owner(uow, world)
    summary = await service.create(
        membership=membership, name="  EU   list prices ", records=(record("a"), record("b", "us-east-1"))
    )
    assert (summary.name, summary.record_count, len(summary.content_hash)) == ("EU list prices", 2, 64)
    stored = await uow.pricing.get(membership.organization_id, summary.id)
    assert stored is not None
    assert stored.content_hash == summary.content_hash
    event = uow.audit.events[-1]
    assert (event.action, event.resource_id) == (AuditAction.PRICING_SNAPSHOT_CREATED, summary.id)
    assert event.metadata == {"records": 2, "content_sha256": summary.content_hash}  # never prices
    assert not hasattr(uow.pricing, "update")  # there is no way to change a snapshot


async def test_viewers_read_but_only_admins_create(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    service = PricingService(uow, clock=clock)
    viewer = await uow.memberships.get_in_active_organization(
        organization_id=world.project.organization_id, user_id=world.vic.id
    )
    assert viewer is not None
    with pytest.raises(PermissionDenied):
        await service.create(membership=viewer.membership, name="Mine", records=(record("a"),))
    summary = await service.create(membership=await owner(uow, world), name="Shared", records=(record("a"),))
    assert (await service.get(membership=viewer.membership, snapshot_id=summary.id)).id == summary.id


async def test_snapshots_of_other_organizations_are_not_found(
    uow: FakeUnitOfWork, clock: FakeClock, world: World
) -> None:
    service = PricingService(uow, clock=clock)
    summary = await service.create(
        membership=await owner(uow, world), name="Acme prices", records=(record("a"),)
    )
    [globex] = await uow.memberships.list_for_user(world.eve.id)
    stranger = globex.membership  # the owner of another organization
    with pytest.raises(PricingSnapshotNotFound):
        await service.get(membership=stranger, snapshot_id=summary.id)
    with pytest.raises(PricingSnapshotNotFound):
        await service.records(membership=stranger, snapshot_id=summary.id, query=RecordQuery())


async def test_listing_and_record_paging(uow: FakeUnitOfWork, clock: FakeClock, world: World) -> None:
    service = PricingService(uow, clock=clock)
    membership = await owner(uow, world)
    ids = []
    for i in range(3):
        ids.append((await service.create(membership=membership, name=f"List {i}", records=(record("a"),))).id)
        clock.advance(timedelta(seconds=1))
    first = await service.list(membership=membership, limit=2)
    rest = await service.list(membership=membership, cursor=first.next_cursor, limit=2)
    assert [s.id for s in (*first.items, *rest.items)] == ids[::-1]
    with pytest.raises(InvalidCursor):
        await service.list(membership=membership, cursor="nope")

    big = await service.create(
        membership=membership,
        name="Big",
        records=tuple(record(f"r{i:02d}", "us-east-1" if i % 2 else "eu-west-1") for i in range(5)),
    )
    page = await service.records(membership=membership, snapshot_id=big.id, query=RecordQuery(limit=2))
    more = await service.records(
        membership=membership, snapshot_id=big.id, query=RecordQuery(limit=10), cursor=page.next_cursor
    )
    assert [r.id for r in (*page.items, *more.items)] == ["r00", "r01", "r02", "r03", "r04"]
    east = await service.records(
        membership=membership, snapshot_id=big.id, query=RecordQuery(region="us-east-1")
    )
    assert [r.id for r in east.items] == ["r01", "r03"]


async def test_invalid_snapshots_are_refused(uow: FakeUnitOfWork, clock: FakeClock, world: World) -> None:
    service = PricingService(uow, clock=clock)
    membership = await owner(uow, world)
    with pytest.raises(InvalidPricingSnapshot):
        await service.create(
            membership=membership, name="Twice", records=(record("a"), record("a", "us-east-1"))
        )
    with pytest.raises(InvalidPricingSnapshot):
        await service.create(membership=membership, name=" ", records=(record("a"),))
    assert uow.pricing.snapshots == {}
