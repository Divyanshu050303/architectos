import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core.domain.audit.audit_service import decode_cursor, encode_cursor
from core.domain.audit.entities import AuditAction, AuditCursor, AuditEvent
from core.domain.audit.errors import InvalidCursor
from core.domain.client import ClientInfo
from core.domain.identity.auth_service import AuthService, VerificationSettings
from core.domain.identity.entities import NewUser, User
from core.domain.identity.errors import AccountDisabled, InvalidCredentials, InvalidRefreshToken
from core.domain.identity.password_service import PasswordService, ResetSettings
from core.domain.identity.passwords import PasswordHasher, PasswordPolicy
from core.domain.identity.session_service import SessionService, SessionSettings
from core.domain.identity.user_service import UserService
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import LastOwner
from core.domain.organizations.invitation_service import InvitationService, InvitationSettings
from core.domain.organizations.membership_service import MembershipService
from core.domain.organizations.organization_service import OrganizationService
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork, RecordingMailer

PASSWORD = "correct horse battery staple"

# --- the event type -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", ["password", "newPassword", "token", "refreshToken", "secret", "passwordHash", "cookie"]
)
def test_metadata_that_looks_secret_is_refused(key: str) -> None:
    with pytest.raises(ValueError, match="secrets"):
        AuditEvent(AuditAction.USER_LOGIN, actor_user_id=None, metadata={key: "x"})


def test_every_action_matches_the_database_format() -> None:
    import re  # noqa: PLC0415

    assert all(re.fullmatch(r"[a-z_]+\.[a-z_]+", action.value) for action in AuditAction)


# --- cursors ------------------------------------------------------------------------------------


def test_cursor_round_trip() -> None:
    cursor = AuditCursor(created_at=datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC), id=uuid.uuid7())
    assert decode_cursor(encode_cursor(cursor)) == cursor


@pytest.mark.parametrize("value", ["", "garbage", "!!!", "bm8tc2VwYXJhdG9y"])
def test_invalid_cursors(value: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(value)


# --- what each flow records ---------------------------------------------------------------------


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
def mailer() -> RecordingMailer:
    return RecordingMailer()


async def verified(uow: FakeUnitOfWork, clock: FakeClock, hasher: PasswordHasher, email: str) -> User:
    user = await uow.users.add(NewUser(email, email[:4], hasher.hash(PASSWORD)))
    await uow.users.mark_email_verified(user.id, clock.now)
    stored = await uow.users.get(user.id)
    assert stored is not None
    return stored


async def test_registration_and_verification(
    uow: FakeUnitOfWork, hasher: PasswordHasher, mailer: RecordingMailer, clock: FakeClock
) -> None:
    auth = AuthService(
        uow,
        hasher=hasher,
        policy=PasswordPolicy(),
        mailer=mailer,
        verification=VerificationSettings(),
        clock=clock,
    )
    await auth.register(email="ada@example.com", password=PASSWORD, name="Ada")
    token = mailer.sent[-1].token
    assert token is not None
    await auth.verify_email(token=token)
    await auth.register(email="ada@example.com", password=PASSWORD, name="Dup")  # duplicate: nothing new

    assert uow.audit.actions() == ["user.registered", "user.email_verified"]


async def test_login_success_failure_and_unknown(
    uow: FakeUnitOfWork, hasher: PasswordHasher, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, hasher, "ada@example.com")
    sessions = SessionService(uow, hasher=hasher, settings=SessionSettings(), clock=clock)

    signed_in = await sessions.login(email="ada@example.com", password=PASSWORD, client=ClientInfo())
    with pytest.raises(InvalidCredentials):
        await sessions.login(email="ada@example.com", password="wrong password!", client=ClientInfo())
    with pytest.raises(InvalidCredentials):
        await sessions.login(email="nobody@example.com", password=PASSWORD, client=ClientInfo())

    assert uow.audit.actions() == ["user.login", "user.login_failed"]
    login, failed = uow.audit.events
    assert (login.actor_user_id, login.resource_id) == (ada.id, signed_in.session.id)
    assert (failed.actor_user_id, failed.resource_id, failed.metadata) == (
        None,
        ada.id,
        {"reason": "wrong_password"},
    )


async def test_login_to_a_disabled_account_is_recorded(
    uow: FakeUnitOfWork, hasher: PasswordHasher, clock: FakeClock
) -> None:
    from dataclasses import replace  # noqa: PLC0415

    from core.domain.identity.enums import UserStatus  # noqa: PLC0415

    ada = await verified(uow, clock, hasher, "ada@example.com")
    uow.users.by_id[ada.id] = replace(ada, status=UserStatus.DISABLED)
    with pytest.raises(AccountDisabled):
        await SessionService(uow, hasher=hasher, settings=SessionSettings(), clock=clock).login(
            email="ada@example.com", password=PASSWORD, client=ClientInfo()
        )
    assert uow.audit.events[-1].metadata == {"reason": "account_disabled"}


async def test_logout_revocation_and_token_reuse(
    uow: FakeUnitOfWork, hasher: PasswordHasher, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, hasher, "ada@example.com")
    sessions = SessionService(uow, hasher=hasher, settings=SessionSettings(), clock=clock)
    first = await sessions.login(email="ada@example.com", password=PASSWORD, client=ClientInfo())
    second = await sessions.login(email="ada@example.com", password=PASSWORD, client=ClientInfo())
    third = await sessions.login(email="ada@example.com", password=PASSWORD, client=ClientInfo())

    await sessions.logout(refresh_token=first.refresh_token)
    await sessions.revoke_session(user_id=ada.id, session_id=second.session.id)
    await sessions.refresh(refresh_token=third.refresh_token)
    clock.advance(timedelta(minutes=1))
    with pytest.raises(InvalidRefreshToken):
        await sessions.refresh(refresh_token=third.refresh_token)  # reuse

    assert uow.audit.actions()[3:] == ["user.logout", "session.revoked", "session.revoked"]
    reuse = uow.audit.events[-1]
    assert reuse.actor_user_id is None
    assert reuse.metadata == {"reason": "token_reuse", "userId": str(ada.id)}


async def test_password_profile_and_deletion(
    uow: FakeUnitOfWork, hasher: PasswordHasher, mailer: RecordingMailer, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, hasher, "ada@example.com")
    passwords = PasswordService(
        uow, hasher=hasher, policy=PasswordPolicy(), mailer=mailer, settings=ResetSettings(), clock=clock
    )
    users = UserService(uow, hasher=hasher, clock=clock)
    new = "tangerine submarine orchestra"

    await passwords.change(
        user_id=ada.id, current_session_id=ada.id, current_password=PASSWORD, new_password=new
    )
    await passwords.request_reset(email="ada@example.com")
    token = mailer.sent[-1].token
    assert token is not None
    await passwords.reset(token=token, new_password=PASSWORD)
    await users.update_profile(user_id=ada.id, name="Ada L.")
    await users.delete_account(user_id=ada.id, password=PASSWORD)

    assert uow.audit.actions() == [
        "user.password_changed",
        "user.password_reset",
        "user.profile_updated",
        "user.deleted",
    ]
    assert uow.audit.events[2].metadata == {"fields": ["name"]}


async def test_organization_member_and_invitation_events(
    uow: FakeUnitOfWork, hasher: PasswordHasher, mailer: RecordingMailer, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, hasher, "ada@example.com")
    orgs = OrganizationService(uow, clock=clock)
    members = MembershipService(uow)
    invitations = InvitationService(uow, mailer=mailer, settings=InvitationSettings(), clock=clock)

    acme = await orgs.create(user=ada, name="Acme")
    org = acme.organization.id
    await orgs.rename(membership=acme.membership, name="Acme Labs")
    revoked = await invitations.invite(
        inviter=ada, organization_id=org, email="x@example.com", role=Role.VIEWER
    )
    await invitations.revoke(organization_id=org, actor_user_id=ada.id, invitation_id=revoked.id)
    await invitations.invite(inviter=ada, organization_id=org, email="bob@example.com", role=Role.MEMBER)
    bob = await verified(uow, clock, hasher, "bob@example.com")
    token = mailer.sent[-1].token
    assert token is not None
    joined = await invitations.accept(user=bob, token=token)
    await members.change_role(
        organization_id=org, actor_user_id=ada.id, member_id=joined.membership.id, role=Role.ADMIN
    )
    await members.remove(organization_id=org, actor_user_id=bob.id, member_id=joined.membership.id)  # leaves
    await orgs.delete(membership=acme.membership)

    assert uow.audit.actions() == [
        "organization.created",
        "organization.updated",
        "member.invited",
        "member.invitation_revoked",
        "member.invited",
        "member.invitation_accepted",
        "member.role_changed",
        "member.removed",
        "organization.deleted",
    ]
    assert all(e.organization_id == org for e in uow.audit.events)
    role_changed, removed = uow.audit.events[6], uow.audit.events[7]
    assert role_changed.metadata == {"userId": str(bob.id), "from": "member", "to": "admin"}
    assert removed.metadata == {"userId": str(bob.id), "role": "admin", "left": True}


async def test_refused_operations_record_nothing(
    uow: FakeUnitOfWork, hasher: PasswordHasher, clock: FakeClock
) -> None:
    ada = await verified(uow, clock, hasher, "ada@example.com")
    acme = await OrganizationService(uow, clock=clock).create(user=ada, name="Acme")
    before = uow.audit.actions()
    with pytest.raises(LastOwner):
        await MembershipService(uow).remove(
            organization_id=acme.organization.id, actor_user_id=ada.id, member_id=acme.membership.id
        )
    assert uow.audit.actions() == before
