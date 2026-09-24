import hashlib
from dataclasses import replace
from datetime import timedelta

import pytest
from argon2 import PasswordHasher as Argon2

from core.domain.identity.entities import NewUser, User
from core.domain.identity.enums import SessionRevocationReason, UserStatus
from core.domain.identity.errors import (
    AccountDisabled,
    InvalidCredentials,
    InvalidRefreshToken,
    RefreshConflict,
    SessionExpired,
    SessionRevoked,
)
from core.domain.identity.passwords import PasswordHasher
from core.domain.identity.session_service import ClientInfo, SessionService, SessionSettings
from core.domain.identity.tokens import format_refresh_token, parse_refresh_token

from .fakes import FakeClock, FakeUnitOfWork

PASSWORD = "correct horse battery staple"
CLIENT = ClientInfo(user_agent="Mozilla/5.0 test", ip_address="203.0.113.7")


class CountingVerifier(PasswordHasher):
    def __init__(self) -> None:
        super().__init__()
        self.verifications = 0

    def verify(self, password_hash: str, password: str) -> bool:
        self.verifications += 1
        return super().verify(password_hash, password)


@pytest.fixture(scope="module")
def hasher() -> CountingVerifier:
    return CountingVerifier()


@pytest.fixture
def sessions(uow: FakeUnitOfWork, hasher: CountingVerifier, clock: FakeClock) -> SessionService:
    return SessionService(uow, hasher=hasher, settings=SessionSettings(), clock=clock)


@pytest.fixture
async def ada(uow: FakeUnitOfWork, hasher: CountingVerifier) -> User:
    return await uow.users.add(NewUser("ada@example.com", "Ada", hasher.hash(PASSWORD)))


# --- refresh token format -----------------------------------------------------------------------


def test_refresh_tokens_round_trip_and_reject_garbage() -> None:
    import uuid  # noqa: PLC0415

    session_id = uuid.uuid7()
    assert parse_refresh_token(format_refresh_token(session_id, "s3cret")) == (session_id, "s3cret")
    for garbage in ["", "no-dot", "not-a-uuid.secret", f"{session_id}.", "x" * 500]:
        assert parse_refresh_token(garbage) is None


# --- login --------------------------------------------------------------------------------------


async def test_login_creates_a_session_and_stores_only_a_hash(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User, clock: FakeClock
) -> None:
    signed_in = await sessions.login(email=" ADA@example.com ", password=PASSWORD, client=CLIENT)

    assert signed_in.user.id == ada.id
    session = uow.sessions.by_id[signed_in.session.id]
    parsed = parse_refresh_token(signed_in.refresh_token)
    assert parsed is not None
    assert parsed[0] == session.id
    assert session.refresh_token_hash == hashlib.sha256(parsed[1].encode()).digest()
    assert parsed[1].encode() not in session.refresh_token_hash
    assert session.expires_at == clock.now + timedelta(days=30)
    assert (session.user_agent, session.ip_address) == (CLIENT.user_agent, CLIENT.ip_address)


async def test_each_login_is_a_separate_session(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User
) -> None:
    first = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    second = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    assert first.session.id != second.session.id
    assert len(uow.sessions.by_id) == 2


async def test_wrong_password_and_unknown_email_fail_identically(
    sessions: SessionService, uow: FakeUnitOfWork, hasher: CountingVerifier, ada: User
) -> None:
    before = hasher.verifications
    errors = []
    for email, password in [
        ("ada@example.com", "wrong password!"),
        ("nobody@example.com", PASSWORD),
        ("junk", PASSWORD),
    ]:
        with pytest.raises(InvalidCredentials) as raised:
            await sessions.login(email=email, password=password, client=CLIENT)
        errors.append((raised.value.code, raised.value.detail_message))

    assert len(set(errors)) == 1
    # Every attempt, including unknown and malformed emails, costs one Argon2 verification.
    assert hasher.verifications - before == 3
    assert uow.sessions.by_id == {}


async def test_disabled_account_is_revealed_only_to_the_right_password(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User
) -> None:
    uow.users.by_id[ada.id] = replace(ada, status=UserStatus.DISABLED)

    with pytest.raises(InvalidCredentials):
        await sessions.login(email="ada@example.com", password="wrong password!", client=CLIENT)
    with pytest.raises(AccountDisabled):
        await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    assert uow.sessions.by_id == {}


async def test_unverified_users_can_sign_in(sessions: SessionService, ada: User) -> None:
    assert not ada.is_email_verified
    signed_in = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    assert signed_in.user.id == ada.id


async def test_login_upgrades_outdated_password_hashes(
    sessions: SessionService, uow: FakeUnitOfWork, hasher: CountingVerifier
) -> None:
    weak = Argon2(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSWORD)
    user = await uow.users.add(NewUser("old@example.com", "Old", weak))

    await sessions.login(email="old@example.com", password=PASSWORD, client=CLIENT)

    upgraded = uow.users.by_id[user.id].password_hash
    assert upgraded != weak
    assert not hasher.needs_rehash(upgraded)
    assert hasher.verify(upgraded, PASSWORD)


async def test_overlong_user_agents_are_truncated(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User
) -> None:
    signed_in = await sessions.login(
        email="ada@example.com", password=PASSWORD, client=ClientInfo(user_agent="x" * 5000)
    )
    assert len(uow.sessions.by_id[signed_in.session.id].user_agent or "") == 512


# --- refresh ------------------------------------------------------------------------------------


async def test_refresh_rotates_the_token(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User, clock: FakeClock
) -> None:
    first = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    clock.advance(timedelta(minutes=20))

    second = await sessions.refresh(refresh_token=first.refresh_token)

    assert second.session.id == first.session.id
    assert second.refresh_token != first.refresh_token
    stored = uow.sessions.by_id[first.session.id]
    assert stored.refreshed_at == clock.now
    assert stored.expires_at == first.session.expires_at  # absolute lifetime: not extended


async def test_previous_token_within_grace_is_a_conflict_not_theft(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User, clock: FakeClock
) -> None:
    first = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    second = await sessions.refresh(refresh_token=first.refresh_token)
    clock.advance(timedelta(seconds=5))

    with pytest.raises(RefreshConflict):
        await sessions.refresh(refresh_token=first.refresh_token)

    assert uow.sessions.by_id[first.session.id].revoked_at is None
    await sessions.refresh(refresh_token=second.refresh_token)  # the current token still works


async def test_reuse_after_grace_revokes_the_whole_session(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User, clock: FakeClock
) -> None:
    stolen = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    legitimate = await sessions.refresh(refresh_token=stolen.refresh_token)
    clock.advance(timedelta(minutes=1))

    with pytest.raises(InvalidRefreshToken):
        await sessions.refresh(refresh_token=stolen.refresh_token)

    stored = uow.sessions.by_id[stolen.session.id]
    assert stored.revoked_reason is SessionRevocationReason.TOKEN_REUSE
    with pytest.raises(InvalidRefreshToken):
        await sessions.refresh(refresh_token=legitimate.refresh_token)  # the family is dead


async def test_unknown_secret_for_a_real_session_does_not_revoke_it(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User
) -> None:
    signed_in = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    forged = format_refresh_token(signed_in.session.id, "guessed-secret")

    with pytest.raises(InvalidRefreshToken):
        await sessions.refresh(refresh_token=forged)

    assert uow.sessions.by_id[signed_in.session.id].revoked_at is None
    await sessions.refresh(refresh_token=signed_in.refresh_token)


@pytest.mark.parametrize("token", ["", "garbage", "00000000-0000-0000-0000-000000000000.secret"])
async def test_malformed_or_unknown_refresh_tokens(sessions: SessionService, token: str) -> None:
    with pytest.raises(InvalidRefreshToken):
        await sessions.refresh(refresh_token=token)


async def test_expired_session_cannot_refresh(sessions: SessionService, ada: User, clock: FakeClock) -> None:
    signed_in = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    clock.advance(timedelta(days=30))

    with pytest.raises(SessionExpired):
        await sessions.refresh(refresh_token=signed_in.refresh_token)


async def test_revoked_session_cannot_refresh(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User, clock: FakeClock
) -> None:
    signed_in = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    await uow.sessions.revoke(signed_in.session.id, reason=SessionRevocationReason.LOGOUT, at=clock.now)

    with pytest.raises(InvalidRefreshToken):
        await sessions.refresh(refresh_token=signed_in.refresh_token)


async def test_disabled_user_cannot_refresh(sessions: SessionService, uow: FakeUnitOfWork, ada: User) -> None:
    signed_in = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    uow.users.by_id[ada.id] = replace(ada, status=UserStatus.DISABLED)

    with pytest.raises(InvalidRefreshToken):
        await sessions.refresh(refresh_token=signed_in.refresh_token)
    # Not treated as theft: the reason stays accurate for the audit trail.
    assert uow.sessions.by_id[signed_in.session.id].revoked_reason is None


# --- authenticate (access-token check) ----------------------------------------------------------


async def test_authenticate_accepts_a_live_session(sessions: SessionService, ada: User) -> None:
    signed_in = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    user, session = await sessions.authenticate(session_id=signed_in.session.id, user_id=ada.id)
    assert (user.id, session.id) == (ada.id, signed_in.session.id)


async def test_authenticate_rejects_revoked_expired_foreign_and_disabled(
    sessions: SessionService, uow: FakeUnitOfWork, ada: User, clock: FakeClock, hasher: CountingVerifier
) -> None:
    signed_in = await sessions.login(email="ada@example.com", password=PASSWORD, client=CLIENT)
    grace = await uow.users.add(NewUser("grace@example.com", "Grace", hasher.hash(PASSWORD)))

    with pytest.raises(SessionRevoked):  # someone else's session id
        await sessions.authenticate(session_id=signed_in.session.id, user_id=grace.id)

    uow.users.by_id[ada.id] = replace(ada, status=UserStatus.DISABLED)
    with pytest.raises(SessionRevoked):
        await sessions.authenticate(session_id=signed_in.session.id, user_id=ada.id)
    uow.users.by_id[ada.id] = ada

    clock.advance(timedelta(days=30))
    with pytest.raises(SessionRevoked):
        await sessions.authenticate(session_id=signed_in.session.id, user_id=ada.id)
