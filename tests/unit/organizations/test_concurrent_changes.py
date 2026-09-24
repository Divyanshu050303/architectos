"""Branches that only occur when something changes between the route's membership check and the
service's own locked re-check: the organization is deleted, or the caller's membership removed."""

import pytest

from core.domain.identity.entities import NewUser, User
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import AlreadyMember, InvalidInvitation, OrganizationNotFound
from core.domain.organizations.invitation_service import InvitationService, InvitationSettings
from core.domain.organizations.membership_service import MembershipService
from core.domain.organizations.organization_service import OrganizationService
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork, RecordingMailer


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


async def verified(uow: FakeUnitOfWork, clock: FakeClock, email: str) -> User:
    user = await uow.users.add(NewUser(email, email[:4], "$argon2id$x"))
    await uow.users.mark_email_verified(user.id, clock.now)
    stored = await uow.users.get(user.id)
    assert stored is not None
    return stored


async def test_organization_deleted_mid_request(uow: FakeUnitOfWork, clock: FakeClock) -> None:
    ada = await verified(uow, clock, "ada@example.com")
    acme = await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")
    org = acme.organization.id
    invitations = InvitationService(uow, mailer=RecordingMailer(), settings=InvitationSettings(), clock=clock)
    pending = await invitations.invite(
        inviter=ada, organization_id=org, email="bob@example.com", role=Role.MEMBER
    )
    uow.organizations.deleted.add(org)

    with pytest.raises(OrganizationNotFound):
        await invitations.invite(inviter=ada, organization_id=org, email="cy@example.com", role=Role.MEMBER)
    with pytest.raises(OrganizationNotFound):
        await invitations.revoke(organization_id=org, actor_user_id=ada.id, invitation_id=pending.id)
    with pytest.raises(OrganizationNotFound):
        await MembershipService(uow).remove(
            organization_id=org, actor_user_id=ada.id, member_id=acme.membership.id
        )


async def test_caller_removed_mid_request(uow: FakeUnitOfWork, clock: FakeClock) -> None:
    ada = await verified(uow, clock, "ada@example.com")
    adam = await verified(uow, clock, "adam@example.com")
    acme = await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")
    org = acme.organization.id
    adam_membership = await uow.memberships.add(organization_id=org, user_id=adam.id, role=Role.ADMIN)
    invitations = InvitationService(uow, mailer=RecordingMailer(), settings=InvitationSettings(), clock=clock)
    pending = await invitations.invite(
        inviter=ada, organization_id=org, email="bob@example.com", role=Role.MEMBER
    )
    await uow.memberships.delete(adam_membership.id)  # removed by Ada a moment earlier

    with pytest.raises(OrganizationNotFound):
        await invitations.invite(inviter=adam, organization_id=org, email="cy@example.com", role=Role.MEMBER)
    with pytest.raises(OrganizationNotFound):
        await invitations.revoke(organization_id=org, actor_user_id=adam.id, invitation_id=pending.id)
    with pytest.raises(OrganizationNotFound):
        await MembershipService(uow).change_role(
            organization_id=org, actor_user_id=adam.id, member_id=acme.membership.id, role=Role.VIEWER
        )


async def test_invitee_joined_another_way_before_accepting(uow: FakeUnitOfWork, clock: FakeClock) -> None:
    ada = await verified(uow, clock, "ada@example.com")
    bob = await verified(uow, clock, "bob@example.com")
    org = (await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")).organization.id
    mailer = RecordingMailer()
    invitations = InvitationService(uow, mailer=mailer, settings=InvitationSettings(), clock=clock)
    await invitations.invite(inviter=ada, organization_id=org, email="bob@example.com", role=Role.MEMBER)
    token = mailer.sent[-1].token
    assert token is not None
    await uow.memberships.add(organization_id=org, user_id=bob.id, role=Role.VIEWER)

    with pytest.raises(AlreadyMember):
        await invitations.accept(user=bob, token=token)
    with pytest.raises(InvalidInvitation):
        await invitations.accept(user=bob, token="x" * 500)


async def test_setting_the_same_role_is_a_no_op_without_an_audit_entry(
    uow: FakeUnitOfWork, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, "ada@example.com")
    bob = await verified(uow, clock, "bob@example.com")
    org = (await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")).organization.id
    bob_membership = await uow.memberships.add(organization_id=org, user_id=bob.id, role=Role.MEMBER)
    before = uow.audit.actions()

    unchanged = await MembershipService(uow).change_role(
        organization_id=org, actor_user_id=ada.id, member_id=bob_membership.id, role=Role.MEMBER
    )

    assert unchanged.role is Role.MEMBER
    assert uow.audit.actions() == before
