import uuid

import pytest

from core.domain.identity.entities import NewUser, User
from core.domain.identity.errors import IncorrectPassword
from core.domain.identity.passwords import PasswordHasher
from core.domain.identity.user_service import UserService
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import (
    LastOwner,
    MemberNotFound,
    OrganizationNotFound,
    PermissionDenied,
    RoleNotManageable,
    SoleOwnerOfOrganization,
)
from core.domain.organizations.membership_service import MembershipService
from core.domain.organizations.organization_service import OrganizationService
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork

PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def members(uow: FakeUnitOfWork) -> MembershipService:
    return MembershipService(uow)


async def user(uow: FakeUnitOfWork, clock: FakeClock, hasher: PasswordHasher, email: str) -> User:
    created = await uow.users.add(NewUser(email, email[:4], hasher.hash(PASSWORD)))
    await uow.users.mark_email_verified(created.id, clock.now)
    stored = await uow.users.get(created.id)
    assert stored is not None
    return stored


async def org_with(uow: FakeUnitOfWork, clock: FakeClock, owner: User) -> uuid.UUID:
    created = await OrganizationService(uow, clock=clock).create(user=owner, name="Acme")
    return created.organization.id


async def test_owner_promotes_and_admin_demotes_below_itself(
    members: MembershipService, uow: FakeUnitOfWork, clock: FakeClock, hasher: PasswordHasher
) -> None:
    ada, bob, cy = [await user(uow, clock, hasher, f"{n}@example.com") for n in ("ada", "bob", "cy")]
    org = await org_with(uow, clock, ada)
    bob_m = await uow.memberships.add(organization_id=org, user_id=bob.id, role=Role.MEMBER)
    cy_m = await uow.memberships.add(organization_id=org, user_id=cy.id, role=Role.MEMBER)

    promoted = await members.change_role(
        organization_id=org, actor_user_id=ada.id, member_id=bob_m.id, role=Role.ADMIN
    )
    assert promoted.role is Role.ADMIN
    demoted = await members.change_role(
        organization_id=org, actor_user_id=bob.id, member_id=cy_m.id, role=Role.VIEWER
    )
    assert demoted.role is Role.VIEWER
    with pytest.raises(RoleNotManageable):
        await members.change_role(
            organization_id=org, actor_user_id=bob.id, member_id=cy_m.id, role=Role.ADMIN
        )


async def test_actor_role_is_reread_not_trusted(
    members: MembershipService, uow: FakeUnitOfWork, clock: FakeClock, hasher: PasswordHasher
) -> None:
    ada, bob = [await user(uow, clock, hasher, f"{n}@example.com") for n in ("ada", "bob")]
    org = await org_with(uow, clock, ada)
    bob_m = await uow.memberships.add(organization_id=org, user_id=bob.id, role=Role.ADMIN)
    viewer = await user(uow, clock, hasher, "vi@example.com")
    vi_m = await uow.memberships.add(organization_id=org, user_id=viewer.id, role=Role.VIEWER)

    await uow.memberships.update_role(bob_m.id, Role.VIEWER)  # demoted since his request started

    # Refused as the viewer he now is, not as the admin he was when the request started.
    with pytest.raises(PermissionDenied):
        await members.remove(organization_id=org, actor_user_id=bob.id, member_id=vi_m.id)
    assert vi_m.id in uow.memberships.by_id


async def test_member_ids_are_scoped_to_the_organization(
    members: MembershipService, uow: FakeUnitOfWork, clock: FakeClock, hasher: PasswordHasher
) -> None:
    ada, grace = [await user(uow, clock, hasher, f"{n}@example.com") for n in ("ada", "grace")]
    acme, globex = await org_with(uow, clock, ada), await org_with(uow, clock, grace)
    grace_owner = await uow.memberships.get_for_user(organization_id=globex, user_id=grace.id)
    assert grace_owner is not None

    with pytest.raises(MemberNotFound):
        await members.remove(organization_id=acme, actor_user_id=ada.id, member_id=grace_owner.id)
    with pytest.raises(OrganizationNotFound):
        await members.remove(organization_id=globex, actor_user_id=ada.id, member_id=grace_owner.id)
    assert grace_owner.id in uow.memberships.by_id


async def test_last_owner_cannot_leave_but_can_after_a_second_owner(
    members: MembershipService, uow: FakeUnitOfWork, clock: FakeClock, hasher: PasswordHasher
) -> None:
    ada, bob = [await user(uow, clock, hasher, f"{n}@example.com") for n in ("ada", "bob")]
    org = await org_with(uow, clock, ada)
    ada_m = await uow.memberships.get_for_user(organization_id=org, user_id=ada.id)
    assert ada_m is not None
    bob_m = await uow.memberships.add(organization_id=org, user_id=bob.id, role=Role.MEMBER)

    with pytest.raises(LastOwner):
        await members.remove(organization_id=org, actor_user_id=ada.id, member_id=ada_m.id)
    await members.change_role(organization_id=org, actor_user_id=ada.id, member_id=bob_m.id, role=Role.OWNER)
    await members.remove(organization_id=org, actor_user_id=ada.id, member_id=ada_m.id)

    assert await uow.memberships.count_owners(org) == 1


# --- account deletion and organizations ---------------------------------------------------------


async def test_sole_owner_of_a_shared_organization_cannot_delete_their_account(
    uow: FakeUnitOfWork, clock: FakeClock, hasher: PasswordHasher
) -> None:
    ada, bob = [await user(uow, clock, hasher, f"{n}@example.com") for n in ("ada", "bob")]
    org = await org_with(uow, clock, ada)
    await uow.memberships.add(organization_id=org, user_id=bob.id, role=Role.MEMBER)

    with pytest.raises(SoleOwnerOfOrganization) as raised:
        await UserService(uow, hasher=hasher, clock=clock).delete_account(user_id=ada.id, password=PASSWORD)

    assert raised.value.details == {"organizationIds": [str(org)]}
    assert uow.users.by_id[ada.id].deleted_at is None


async def test_deleting_an_account_removes_memberships_and_solo_organizations(
    uow: FakeUnitOfWork, clock: FakeClock, hasher: PasswordHasher
) -> None:
    ada, grace = [await user(uow, clock, hasher, f"{n}@example.com") for n in ("ada", "grace")]
    solo = await org_with(uow, clock, ada)
    shared = await org_with(uow, clock, grace)
    await uow.memberships.add(organization_id=shared, user_id=ada.id, role=Role.MEMBER)

    await UserService(uow, hasher=hasher, clock=clock).delete_account(user_id=ada.id, password=PASSWORD)

    assert solo in uow.organizations.deleted
    assert shared not in uow.organizations.deleted
    assert all(m.user_id != ada.id for m in uow.memberships.by_id.values())


async def test_wrong_password_checks_happen_before_organization_rules(
    uow: FakeUnitOfWork, clock: FakeClock, hasher: PasswordHasher
) -> None:
    ada = await user(uow, clock, hasher, "ada@example.com")
    with pytest.raises(IncorrectPassword):
        await UserService(uow, hasher=hasher, clock=clock).delete_account(
            user_id=ada.id, password="nope nope nope"
        )
