from dataclasses import replace

import pytest

from core.domain.identity.entities import NewUser, User
from core.domain.organizations.entities import Membership
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import (
    EmailNotVerified,
    InvalidOrganizationName,
    OrganizationNotFound,
    PermissionDenied,
)
from core.domain.organizations.organization_service import OrganizationService
from core.domain.organizations.value_objects import normalize_organization_name
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def organizations(uow: FakeUnitOfWork, clock: FakeClock) -> OrganizationService:
    return OrganizationService(uow, clock=clock)


async def verified_user(uow: FakeUnitOfWork, clock: FakeClock, email: str = "ada@example.com") -> User:
    user = await uow.users.add(NewUser(email, "Ada", "$argon2id$x"))
    await uow.users.mark_email_verified(user.id, clock.now)
    stored = await uow.users.get(user.id)
    assert stored is not None
    return stored


def as_role(membership: Membership, role: Role) -> Membership:
    return replace(membership, role=role)


@pytest.mark.parametrize(("raw", "expected"), [("  Acme   Eng ", "Acme Eng"), ("Zoë Labs", "Zoë Labs")])
def test_names_are_normalized(raw: str, expected: str) -> None:
    assert normalize_organization_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "x" * 101, "Acme\u0000"])
def test_invalid_names(raw: str) -> None:
    with pytest.raises(InvalidOrganizationName):
        normalize_organization_name(raw)


async def test_creator_becomes_owner(
    organizations: OrganizationService, uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified_user(uow, clock)

    created = await organizations.create(user=ada, name=" Acme ")

    assert created.organization.name == "Acme"
    assert (created.membership.user_id, created.membership.role) == (ada.id, Role.OWNER)
    assert uow.commits == 1


async def test_unverified_users_cannot_create(
    organizations: OrganizationService, uow: FakeUnitOfWork
) -> None:
    unverified = await uow.users.add(NewUser("new@example.com", "New", "$argon2id$x"))
    with pytest.raises(EmailNotVerified):
        await organizations.create(user=unverified, name="Acme")
    assert uow.organizations.by_id == {}


async def test_resolve_requires_membership(
    organizations: OrganizationService, uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified_user(uow, clock)
    grace = await verified_user(uow, clock, "grace@example.com")
    acme = await organizations.create(user=ada, name="Acme")

    assert (
        await organizations.resolve(organization_id=acme.organization.id, user_id=ada.id)
    ).membership.role is Role.OWNER
    with pytest.raises(OrganizationNotFound):
        await organizations.resolve(organization_id=acme.organization.id, user_id=grace.id)


@pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
async def test_rename_is_refused_below_admin(
    organizations: OrganizationService, uow: FakeUnitOfWork, clock: FakeClock, role: Role
) -> None:
    ada = await verified_user(uow, clock)
    acme = await organizations.create(user=ada, name="Acme")
    with pytest.raises(PermissionDenied):
        await organizations.rename(membership=as_role(acme.membership, role), name="Hijacked")
    assert uow.organizations.by_id[acme.organization.id].name == "Acme"


async def test_admins_can_rename(
    organizations: OrganizationService, uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified_user(uow, clock)
    acme = await organizations.create(user=ada, name="Acme")
    renamed = await organizations.rename(membership=as_role(acme.membership, Role.ADMIN), name="Acme Labs")
    assert renamed.name == "Acme Labs"


@pytest.mark.parametrize("role", [Role.ADMIN, Role.MEMBER, Role.VIEWER])
async def test_only_owners_delete(
    organizations: OrganizationService, uow: FakeUnitOfWork, clock: FakeClock, role: Role
) -> None:
    ada = await verified_user(uow, clock)
    acme = await organizations.create(user=ada, name="Acme")
    with pytest.raises(PermissionDenied):
        await organizations.delete(membership=as_role(acme.membership, role))
    assert uow.organizations.deleted == set()


async def test_deleted_organizations_disappear(
    organizations: OrganizationService, uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified_user(uow, clock)
    acme = await organizations.create(user=ada, name="Acme")

    await organizations.delete(membership=acme.membership)

    assert await organizations.list_for_user(user_id=ada.id) == []
    with pytest.raises(OrganizationNotFound):
        await organizations.resolve(organization_id=acme.organization.id, user_id=ada.id)
