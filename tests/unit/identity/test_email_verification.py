from dataclasses import replace
from datetime import timedelta

import pytest

from core.domain.identity.auth_service import AuthService
from core.domain.identity.entities import User
from core.domain.identity.enums import UserStatus
from core.domain.identity.errors import InvalidToken, TokenExpired
from core.domain.identity.tokens import generate_token, hash_token

from .fakes import FakeClock, FakeUnitOfWork, RecordingMailer

PASSWORD = "correct horse battery staple"


async def register(service: AuthService, mailer: RecordingMailer, email: str = "ada@example.com") -> str:
    await service.register(email=email, password=PASSWORD, name="Ada")
    token = mailer.sent[-1].token
    assert token is not None
    return token


def only_user(uow: FakeUnitOfWork) -> User:
    [user] = uow.users.by_id.values()
    return user


# --- tokens -------------------------------------------------------------------------------------


def test_tokens_are_long_random_and_url_safe() -> None:
    tokens = {generate_token() for _ in range(1000)}
    assert len(tokens) == 1000
    for token in tokens:
        assert len(token) >= 43  # 32 bytes, base64url
        assert token.replace("-", "").replace("_", "").isalnum()


def test_token_hash_is_a_stable_sha256_digest() -> None:
    token = generate_token()
    assert hash_token(token) == hash_token(token)
    assert len(hash_token(token)) == 32
    assert hash_token(token) != hash_token(generate_token())


# --- verify -------------------------------------------------------------------------------------


async def test_valid_token_verifies_the_email(
    service: AuthService, uow: FakeUnitOfWork, mailer: RecordingMailer, clock: FakeClock
) -> None:
    token = await register(service, mailer)
    clock.advance(timedelta(hours=1))

    await service.verify_email(token=token)

    assert only_user(uow).email_verified_at == clock.now


async def test_token_cannot_be_used_twice(service: AuthService, mailer: RecordingMailer) -> None:
    token = await register(service, mailer)
    await service.verify_email(token=token)

    with pytest.raises(InvalidToken):
        await service.verify_email(token=token)


@pytest.mark.parametrize("token", ["", "unknown-token", "x" * 500])
async def test_unknown_or_malformed_tokens_are_invalid(service: AuthService, token: str) -> None:
    with pytest.raises(InvalidToken):
        await service.verify_email(token=token)


async def test_expired_token_is_rejected_and_the_user_stays_unverified(
    service: AuthService, uow: FakeUnitOfWork, mailer: RecordingMailer, clock: FakeClock
) -> None:
    token = await register(service, mailer)
    clock.advance(timedelta(hours=24))

    with pytest.raises(TokenExpired):
        await service.verify_email(token=token)
    assert only_user(uow).email_verified_at is None


async def test_token_of_a_disabled_account_is_invalid(
    service: AuthService, uow: FakeUnitOfWork, mailer: RecordingMailer
) -> None:
    token = await register(service, mailer)
    user = only_user(uow)
    uow.users.by_id[user.id] = replace(user, status=UserStatus.DISABLED)

    with pytest.raises(InvalidToken):
        await service.verify_email(token=token)


# --- resend -------------------------------------------------------------------------------------


async def test_resend_replaces_the_previous_link(
    service: AuthService, mailer: RecordingMailer, clock: FakeClock
) -> None:
    old = await register(service, mailer)
    clock.advance(timedelta(minutes=5))

    await service.resend_verification(email=" ADA@example.com")
    new = mailer.sent[-1].token
    assert new is not None
    assert new != old

    with pytest.raises(InvalidToken):
        await service.verify_email(token=old)
    await service.verify_email(token=new)


async def test_repeated_resends_within_the_cooldown_send_one_email(
    service: AuthService, mailer: RecordingMailer, clock: FakeClock
) -> None:
    await register(service, mailer)
    clock.advance(timedelta(minutes=5))

    for _ in range(5):
        await service.resend_verification(email="ada@example.com")
        clock.advance(timedelta(seconds=10))

    assert len(mailer.sent) == 2  # registration + one resend


@pytest.mark.parametrize("email", ["nobody@example.com", "not an email", ""])
async def test_resend_for_unknown_or_invalid_emails_is_silent(
    service: AuthService, mailer: RecordingMailer, email: str
) -> None:
    await service.resend_verification(email=email)
    assert mailer.sent == []


async def test_resend_for_a_verified_account_is_silent(
    service: AuthService, mailer: RecordingMailer, clock: FakeClock
) -> None:
    token = await register(service, mailer)
    await service.verify_email(token=token)
    clock.advance(timedelta(minutes=5))

    await service.resend_verification(email="ada@example.com")
    assert len(mailer.sent) == 1


async def test_verifying_invalidates_other_outstanding_links(
    service: AuthService, uow: FakeUnitOfWork, mailer: RecordingMailer
) -> None:
    token = await register(service, mailer)
    await service.verify_email(token=token)

    assert all(t.is_spent for t in uow.email_verification_tokens.tokens.values())
