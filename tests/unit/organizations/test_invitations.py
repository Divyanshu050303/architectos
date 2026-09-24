import itertools
import uuid
from datetime import timedelta

import pytest

from core.domain.errors import DomainError
from core.domain.identity.entities import NewUser, User
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import (
    AlreadyMember,
    EmailNotVerified,
    InvalidInvitation,
    InvitationEmailMismatch,
    InvitationExpired,
    InvitationNotFound,
    OwnerInvitationNotAllowed,
    PermissionDenied,
    RoleNotManageable,
)
from core.domain.organizations.invitation_service import InvitationService, InvitationSettings
from core.domain.organizations.membership_policy import check_invitation
from core.domain.organizations.organization_service import OrganizationService
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork, RecordingMailer

OWNER, ADMIN, MEMBER, VIEWER = Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER


# --- policy -------------------------------------------------------------------------------------


def expected(actor: Role, role: Role) -> type[DomainError] | None:
    if actor not in {OWNER, ADMIN}:
        return PermissionDenied
    if role is OWNER:
        return OwnerInvitationNotAllowed
    if actor is ADMIN and role is ADMIN:
        return RoleNotManageable
    return None


@pytest.mark.parametrize(("actor", "role"), list(itertools.product(list(Role), list(Role))))
def test_invitation_policy(actor: Role, role: Role) -> None:
    error = expected(actor, role)
    if error is None:
        check_invitation(actor=actor, role=role)
    else:
        with pytest.raises(error):
            check_invitation(actor=actor, role=role)


# --- service ------------------------------------------------------------------------------------


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def mailer() -> RecordingMailer:
    return RecordingMailer()


@pytest.fixture
def invitations(uow: FakeUnitOfWork, mailer: RecordingMailer, clock: FakeClock) -> InvitationService:
    return InvitationService(uow, mailer=mailer, settings=InvitationSettings(), clock=clock)


async def person(uow: FakeUnitOfWork, clock: FakeClock, email: str, *, verified: bool = True) -> User:
    created = await uow.users.add(NewUser(email, email[:4], "$argon2id$x"))
    if verified:
        await uow.users.mark_email_verified(created.id, clock.now)
    stored = await uow.users.get(created.id)
    assert stored is not None
    return stored


@pytest.fixture
async def ada(uow: FakeUnitOfWork, clock: FakeClock) -> User:
    return await person(uow, clock, "ada@example.com")


@pytest.fixture
async def acme(uow: FakeUnitOfWork, clock: FakeClock, ada: User) -> uuid.UUID:
    return (await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")).organization.id


def last_token(mailer: RecordingMailer) -> str:
    token = mailer.sent[-1].token
    assert token is not None
    return token


async def test_invite_and_accept(
    invitations: InvitationService,
    uow: FakeUnitOfWork,
    mailer: RecordingMailer,
    clock: FakeClock,
    ada: User,
    acme: uuid.UUID,
) -> None:
    invitation = await invitations.invite(
        inviter=ada, organization_id=acme, email=" BOB@example.com ", role=MEMBER
    )
    assert (invitation.email, invitation.role) == ("bob@example.com", MEMBER)
    assert mailer.sent[-1].to == "bob@example.com"
    bob = await person(uow, clock, "bob@example.com")

    joined = await invitations.accept(user=bob, token=last_token(mailer))

    assert (joined.organization.id, joined.membership.role) == (acme, MEMBER)
    assert uow.invitations.by_id[invitation.id].accepted_at == clock.now


async def test_accepting_twice(
    invitations: InvitationService,
    uow: FakeUnitOfWork,
    mailer: RecordingMailer,
    clock: FakeClock,
    ada: User,
    acme: uuid.UUID,
) -> None:
    await invitations.invite(inviter=ada, organization_id=acme, email="bob@example.com", role=MEMBER)
    bob = await person(uow, clock, "bob@example.com")
    token = last_token(mailer)
    await invitations.accept(user=bob, token=token)
    with pytest.raises(InvalidInvitation):
        await invitations.accept(user=bob, token=token)


async def test_email_must_match_and_be_verified(
    invitations: InvitationService,
    uow: FakeUnitOfWork,
    mailer: RecordingMailer,
    clock: FakeClock,
    ada: User,
    acme: uuid.UUID,
) -> None:
    await invitations.invite(inviter=ada, organization_id=acme, email="bob@example.com", role=MEMBER)
    token = last_token(mailer)
    mallory = await person(uow, clock, "mallory@example.com")
    unverified_bob = await person(uow, clock, "bob@example.com", verified=False)

    with pytest.raises(InvitationEmailMismatch):
        await invitations.accept(user=mallory, token=token)
    with pytest.raises(EmailNotVerified):
        await invitations.accept(user=unverified_bob, token=token)
    assert len(uow.memberships.by_id) == 1  # only Ada


async def test_expired_revoked_and_unknown_invitations(
    invitations: InvitationService,
    uow: FakeUnitOfWork,
    mailer: RecordingMailer,
    clock: FakeClock,
    ada: User,
    acme: uuid.UUID,
) -> None:
    bob = await person(uow, clock, "bob@example.com")
    first = await invitations.invite(inviter=ada, organization_id=acme, email="bob@example.com", role=MEMBER)
    first_token = last_token(mailer)
    await invitations.invite(
        inviter=ada, organization_id=acme, email="bob@example.com", role=VIEWER
    )  # re-invite
    second_token = last_token(mailer)

    with pytest.raises(InvalidInvitation):  # superseded by the re-invite
        await invitations.accept(user=bob, token=first_token)
    assert uow.invitations.by_id[first.id].revoked_at is not None
    with pytest.raises(InvalidInvitation):
        await invitations.accept(user=bob, token="not-a-real-token")
    clock.advance(timedelta(days=7))
    with pytest.raises(InvitationExpired):
        await invitations.accept(user=bob, token=second_token)


async def test_invitation_to_a_deleted_organization(
    invitations: InvitationService,
    uow: FakeUnitOfWork,
    mailer: RecordingMailer,
    clock: FakeClock,
    ada: User,
    acme: uuid.UUID,
) -> None:
    await invitations.invite(inviter=ada, organization_id=acme, email="bob@example.com", role=MEMBER)
    bob = await person(uow, clock, "bob@example.com")
    uow.organizations.deleted.add(acme)
    with pytest.raises(InvalidInvitation):
        await invitations.accept(user=bob, token=last_token(mailer))


async def test_existing_members_cannot_be_invited(
    invitations: InvitationService, uow: FakeUnitOfWork, clock: FakeClock, ada: User, acme: uuid.UUID
) -> None:
    bob = await person(uow, clock, "bob@example.com")
    await uow.memberships.add(organization_id=acme, user_id=bob.id, role=VIEWER)
    for email in ("ada@example.com", "BOB@example.com"):
        with pytest.raises(AlreadyMember):
            await invitations.invite(inviter=ada, organization_id=acme, email=email, role=MEMBER)


async def test_admins_invite_below_themselves_and_revoke_likewise(
    invitations: InvitationService, uow: FakeUnitOfWork, clock: FakeClock, ada: User, acme: uuid.UUID
) -> None:
    adam = await person(uow, clock, "adam@example.com")
    await uow.memberships.add(organization_id=acme, user_id=adam.id, role=ADMIN)

    with pytest.raises(RoleNotManageable):
        await invitations.invite(inviter=adam, organization_id=acme, email="x@example.com", role=ADMIN)
    by_owner = await invitations.invite(inviter=ada, organization_id=acme, email="x@example.com", role=ADMIN)
    with pytest.raises(RoleNotManageable):
        await invitations.revoke(organization_id=acme, actor_user_id=adam.id, invitation_id=by_owner.id)

    by_admin = await invitations.invite(
        inviter=adam, organization_id=acme, email="y@example.com", role=MEMBER
    )
    await invitations.revoke(organization_id=acme, actor_user_id=adam.id, invitation_id=by_admin.id)
    with pytest.raises(InvitationNotFound):
        await invitations.revoke(organization_id=acme, actor_user_id=adam.id, invitation_id=by_admin.id)


async def test_invitation_ids_are_scoped_to_the_organization(
    invitations: InvitationService, uow: FakeUnitOfWork, clock: FakeClock, ada: User, acme: uuid.UUID
) -> None:
    grace = await person(uow, clock, "grace@example.com")
    globex = (await OrganizationService(uow, clock=clock).create(user=grace, name="Globex")).organization.id
    theirs = await invitations.invite(
        inviter=grace, organization_id=globex, email="z@example.com", role=MEMBER
    )

    with pytest.raises(InvitationNotFound):
        await invitations.revoke(organization_id=acme, actor_user_id=ada.id, invitation_id=theirs.id)
    assert uow.invitations.by_id[theirs.id].is_pending()
