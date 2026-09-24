from dataclasses import replace
from datetime import timedelta

import pytest

from core.domain.identity.entities import NewUser, User
from core.domain.identity.enums import SessionRevocationReason, UserStatus
from core.domain.identity.errors import IncorrectPassword, InvalidToken, TokenExpired, WeakPassword
from core.domain.identity.password_service import PasswordService, ResetSettings
from core.domain.identity.passwords import PasswordHasher, PasswordPolicy
from core.domain.identity.session_service import ClientInfo, SessionService, SessionSettings

from .fakes import FakeClock, FakeUnitOfWork, RecordingMailer

OLD = "correct horse battery staple"
NEW = "tangerine submarine orchestra"


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher()


@pytest.fixture
def passwords(
    uow: FakeUnitOfWork, hasher: PasswordHasher, mailer: RecordingMailer, clock: FakeClock
) -> PasswordService:
    return PasswordService(
        uow, hasher=hasher, policy=PasswordPolicy(), mailer=mailer, settings=ResetSettings(), clock=clock
    )


@pytest.fixture
def sessions(uow: FakeUnitOfWork, hasher: PasswordHasher, clock: FakeClock) -> SessionService:
    return SessionService(uow, hasher=hasher, settings=SessionSettings(), clock=clock)


@pytest.fixture
async def ada(uow: FakeUnitOfWork, hasher: PasswordHasher) -> User:
    return await uow.users.add(NewUser("ada@example.com", "Ada", hasher.hash(OLD)))


async def reset_token(passwords: PasswordService, mailer: RecordingMailer) -> str:
    await passwords.request_reset(email="ada@example.com")
    token = mailer.sent[-1].token
    assert token is not None
    return token


def password_works(uow: FakeUnitOfWork, hasher: PasswordHasher, user: User, password: str) -> bool:
    return hasher.verify(uow.users.by_id[user.id].password_hash, password)


# --- forgot password ----------------------------------------------------------------------------


async def test_request_emails_a_single_use_link(
    passwords: PasswordService, uow: FakeUnitOfWork, mailer: RecordingMailer, ada: User, clock: FakeClock
) -> None:
    await passwords.request_reset(email=" ADA@example.com ")

    [email] = mailer.sent
    assert (email.kind, email.to) == ("password_reset", "ada@example.com")
    [stored] = uow.password_reset_tokens.tokens.values()
    assert stored.expires_at == clock.now + timedelta(minutes=30)
    assert len(stored.token_hash) == 32


@pytest.mark.parametrize("email", ["nobody@example.com", "not an email", ""])
async def test_request_for_unknown_or_invalid_email_is_silent(
    passwords: PasswordService, mailer: RecordingMailer, email: str
) -> None:
    await passwords.request_reset(email=email)
    assert mailer.sent == []


async def test_request_for_a_disabled_account_is_silent(
    passwords: PasswordService, uow: FakeUnitOfWork, mailer: RecordingMailer, ada: User
) -> None:
    uow.users.by_id[ada.id] = replace(ada, status=UserStatus.DISABLED)
    await passwords.request_reset(email="ada@example.com")
    assert mailer.sent == []


async def test_repeated_requests_are_throttled_and_supersede_old_links(
    passwords: PasswordService, mailer: RecordingMailer, ada: User, clock: FakeClock
) -> None:
    first = await reset_token(passwords, mailer)
    for _ in range(3):
        await passwords.request_reset(email="ada@example.com")
    assert len(mailer.sent) == 1

    clock.advance(timedelta(seconds=61))
    second = await reset_token(passwords, mailer)
    assert second != first
    with pytest.raises(InvalidToken):
        await passwords.reset(token=first, new_password=NEW)


# --- reset --------------------------------------------------------------------------------------


async def test_reset_changes_the_password_and_signs_out_everywhere(
    passwords: PasswordService,
    sessions: SessionService,
    uow: FakeUnitOfWork,
    hasher: PasswordHasher,
    mailer: RecordingMailer,
    ada: User,
) -> None:
    laptop = await sessions.login(email="ada@example.com", password=OLD, client=ClientInfo())
    phone = await sessions.login(email="ada@example.com", password=OLD, client=ClientInfo())
    token = await reset_token(passwords, mailer)

    await passwords.reset(token=token, new_password=NEW)

    assert password_works(uow, hasher, ada, NEW)
    assert not password_works(uow, hasher, ada, OLD)
    for signed_in in (laptop, phone):
        assert (
            uow.sessions.by_id[signed_in.session.id].revoked_reason is SessionRevocationReason.PASSWORD_RESET
        )
    assert mailer.sent[-1].kind == "password_changed"
    assert uow.users.by_id[ada.id].is_email_verified  # the link proved inbox control


async def test_reset_token_is_single_use(
    passwords: PasswordService, mailer: RecordingMailer, ada: User
) -> None:
    token = await reset_token(passwords, mailer)
    await passwords.reset(token=token, new_password=NEW)
    with pytest.raises(InvalidToken):
        await passwords.reset(token=token, new_password="another fine password")


async def test_expired_reset_token(
    passwords: PasswordService,
    uow: FakeUnitOfWork,
    hasher: PasswordHasher,
    mailer: RecordingMailer,
    ada: User,
    clock: FakeClock,
) -> None:
    token = await reset_token(passwords, mailer)
    clock.advance(timedelta(minutes=30))
    with pytest.raises(TokenExpired):
        await passwords.reset(token=token, new_password=NEW)
    assert password_works(uow, hasher, ada, OLD)


@pytest.mark.parametrize("token", ["", "unknown", "x" * 500])
async def test_invalid_reset_tokens(passwords: PasswordService, token: str) -> None:
    with pytest.raises(InvalidToken):
        await passwords.reset(token=token, new_password=NEW)


async def test_weak_password_keeps_the_link_usable(
    passwords: PasswordService,
    uow: FakeUnitOfWork,
    hasher: PasswordHasher,
    mailer: RecordingMailer,
    ada: User,
) -> None:
    token = await reset_token(passwords, mailer)
    with pytest.raises(WeakPassword):
        await passwords.reset(token=token, new_password="short")
    with pytest.raises(WeakPassword):
        await passwords.reset(token=token, new_password="ada@example.com rocks")  # contains the email

    await passwords.reset(token=token, new_password=NEW)
    assert password_works(uow, hasher, ada, NEW)


async def test_reset_for_a_disabled_account_is_refused(
    passwords: PasswordService, uow: FakeUnitOfWork, mailer: RecordingMailer, ada: User
) -> None:
    token = await reset_token(passwords, mailer)
    uow.users.by_id[ada.id] = replace(ada, status=UserStatus.DISABLED)
    with pytest.raises(InvalidToken):
        await passwords.reset(token=token, new_password=NEW)


# --- change -------------------------------------------------------------------------------------


async def test_change_keeps_this_session_and_signs_out_the_others(
    passwords: PasswordService,
    sessions: SessionService,
    uow: FakeUnitOfWork,
    hasher: PasswordHasher,
    mailer: RecordingMailer,
    ada: User,
) -> None:
    here = await sessions.login(email="ada@example.com", password=OLD, client=ClientInfo())
    elsewhere = await sessions.login(email="ada@example.com", password=OLD, client=ClientInfo())

    await passwords.change(
        user_id=ada.id, current_session_id=here.session.id, current_password=OLD, new_password=NEW
    )

    assert password_works(uow, hasher, ada, NEW)
    assert uow.sessions.by_id[here.session.id].revoked_at is None
    assert uow.sessions.by_id[elsewhere.session.id].revoked_reason is SessionRevocationReason.PASSWORD_CHANGED
    await sessions.refresh(refresh_token=here.refresh_token)  # still works
    assert [e.kind for e in mailer.sent] == ["password_changed"]


async def test_change_requires_the_current_password(
    passwords: PasswordService,
    uow: FakeUnitOfWork,
    hasher: PasswordHasher,
    mailer: RecordingMailer,
    ada: User,
) -> None:
    with pytest.raises(IncorrectPassword):
        await passwords.change(
            user_id=ada.id, current_session_id=ada.id, current_password="not my password", new_password=NEW
        )
    assert password_works(uow, hasher, ada, OLD)
    assert mailer.sent == []


async def test_change_enforces_the_policy(passwords: PasswordService, ada: User) -> None:
    with pytest.raises(WeakPassword):
        await passwords.change(
            user_id=ada.id, current_session_id=ada.id, current_password=OLD, new_password="password1234"
        )


async def test_change_invalidates_pending_reset_links(
    passwords: PasswordService, mailer: RecordingMailer, ada: User
) -> None:
    token = await reset_token(passwords, mailer)
    await passwords.change(user_id=ada.id, current_session_id=ada.id, current_password=OLD, new_password=NEW)
    with pytest.raises(InvalidToken):
        await passwords.reset(token=token, new_password="yet another password")
