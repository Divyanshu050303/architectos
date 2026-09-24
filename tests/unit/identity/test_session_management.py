from datetime import timedelta

import pytest

from core.domain.identity.entities import NewUser, User
from core.domain.identity.enums import SessionRevocationReason
from core.domain.identity.errors import InvalidRefreshToken, SessionNotFound
from core.domain.identity.passwords import PasswordHasher
from core.domain.identity.session_service import ClientInfo, SessionService, SessionSettings, SignedIn
from core.domain.identity.tokens import format_refresh_token

from .fakes import FakeClock, FakeUnitOfWork

PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher()


@pytest.fixture
def sessions(uow: FakeUnitOfWork, hasher: PasswordHasher, clock: FakeClock) -> SessionService:
    return SessionService(uow, hasher=hasher, settings=SessionSettings(), clock=clock)


@pytest.fixture
async def ada(uow: FakeUnitOfWork, hasher: PasswordHasher) -> User:
    return await uow.users.add(NewUser("ada@example.com", "Ada", hasher.hash(PASSWORD)))


@pytest.fixture
async def grace(uow: FakeUnitOfWork, hasher: PasswordHasher) -> User:
    return await uow.users.add(NewUser("grace@example.com", "Grace", hasher.hash(PASSWORD)))


async def sign_in(sessions: SessionService, user: User) -> SignedIn:
    return await sessions.login(email=user.email, password=PASSWORD, client=ClientInfo())


# --- logout -------------------------------------------------------------------------------------


async def test_logout_revokes_the_session(sessions: SessionService, uow: FakeUnitOfWork, ada: User) -> None:
    signed_in = await sign_in(sessions, ada)

    await sessions.logout(refresh_token=signed_in.refresh_token)

    assert uow.sessions.by_id[signed_in.session.id].revoked_reason is SessionRevocationReason.LOGOUT
    with pytest.raises(InvalidRefreshToken):
        await sessions.refresh(refresh_token=signed_in.refresh_token)


async def test_logout_accepts_the_just_rotated_token(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User
) -> None:
    # A tab still holding the previous cookie can still sign the browser out.
    first = await sign_in(sessions, ada)
    await sessions.refresh(refresh_token=first.refresh_token)

    await sessions.logout(refresh_token=first.refresh_token)
    assert uow.sessions.by_id[first.session.id].revoked_at is not None


async def test_logout_is_idempotent(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User, clock: FakeClock
) -> None:
    signed_in = await sign_in(sessions, ada)
    await sessions.logout(refresh_token=signed_in.refresh_token)
    revoked_at = uow.sessions.by_id[signed_in.session.id].revoked_at
    clock.advance(timedelta(minutes=1))

    await sessions.logout(refresh_token=signed_in.refresh_token)
    assert uow.sessions.by_id[signed_in.session.id].revoked_at == revoked_at


@pytest.mark.parametrize("token", [None, "", "garbage", "00000000-0000-0000-0000-000000000000.secret"])
async def test_logout_without_a_usable_token_is_a_no_op(sessions: SessionService, token: str | None) -> None:
    await sessions.logout(refresh_token=token)


async def test_logout_with_a_forged_secret_revokes_nothing(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User
) -> None:
    signed_in = await sign_in(sessions, ada)
    await sessions.logout(refresh_token=format_refresh_token(signed_in.session.id, "guessed"))
    assert uow.sessions.by_id[signed_in.session.id].revoked_at is None


# --- listing ------------------------------------------------------------------------------------


async def test_list_shows_only_my_active_sessions_most_recent_first(
    sessions: SessionService, ada: User, grace: User, clock: FakeClock
) -> None:
    older = await sign_in(sessions, ada)
    clock.advance(timedelta(minutes=1))
    newer = await sign_in(sessions, ada)
    clock.advance(timedelta(minutes=1))
    logged_out = await sign_in(sessions, ada)
    await sessions.logout(refresh_token=logged_out.refresh_token)
    await sign_in(sessions, grace)

    listed = await sessions.list_sessions(user_id=ada.id)
    assert [s.id for s in listed] == [newer.session.id, older.session.id]

    clock.advance(timedelta(minutes=5))
    await sessions.refresh(refresh_token=older.refresh_token)  # using a session moves it up
    listed = await sessions.list_sessions(user_id=ada.id)
    assert [s.id for s in listed] == [older.session.id, newer.session.id]


async def test_expired_sessions_are_not_listed(sessions: SessionService, ada: User, clock: FakeClock) -> None:
    await sign_in(sessions, ada)
    clock.advance(timedelta(days=30))
    assert await sessions.list_sessions(user_id=ada.id) == []


# --- revoking one session -----------------------------------------------------------------------


async def test_revoke_my_own_session(sessions: SessionService, uow: FakeUnitOfWork, ada: User) -> None:
    signed_in = await sign_in(sessions, ada)

    await sessions.revoke_session(user_id=ada.id, session_id=signed_in.session.id)

    assert uow.sessions.by_id[signed_in.session.id].revoked_reason is SessionRevocationReason.USER_REVOKED


async def test_cannot_revoke_someone_elses_session(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User, grace: User
) -> None:
    graces = await sign_in(sessions, grace)

    with pytest.raises(SessionNotFound):
        await sessions.revoke_session(user_id=ada.id, session_id=graces.session.id)
    assert uow.sessions.by_id[graces.session.id].revoked_at is None


async def test_revoking_twice_or_an_expired_session_is_not_found(
    sessions: SessionService, ada: User, clock: FakeClock
) -> None:
    first, second = await sign_in(sessions, ada), await sign_in(sessions, ada)
    await sessions.revoke_session(user_id=ada.id, session_id=first.session.id)

    with pytest.raises(SessionNotFound):
        await sessions.revoke_session(user_id=ada.id, session_id=first.session.id)
    clock.advance(timedelta(days=30))
    with pytest.raises(SessionNotFound):
        await sessions.revoke_session(user_id=ada.id, session_id=second.session.id)
