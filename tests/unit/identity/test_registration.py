from dataclasses import replace
from datetime import timedelta

import pytest

from core.domain.identity.auth_service import AuthService
from core.domain.identity.enums import UserStatus
from core.domain.identity.errors import InvalidEmail, InvalidName, WeakPassword

from .conftest import CountingHasher
from .fakes import FakeClock, FakeUnitOfWork, RecordingMailer

PASSWORD = "correct horse battery staple"


async def test_registration_stores_normalized_email_and_a_hash(
    service: AuthService, uow: FakeUnitOfWork
) -> None:
    result = await service.register(email="  Ada@Example.COM ", password=PASSWORD, name=" Ada  Lovelace ")

    assert result.user is not None
    assert result.user.email == "ada@example.com"
    assert result.user.name == "Ada Lovelace"
    assert result.user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in result.user.password_hash
    assert not result.user.is_email_verified
    assert uow.commits == 1


async def test_registration_sends_a_verification_link(
    service: AuthService, uow: FakeUnitOfWork, mailer: RecordingMailer
) -> None:
    await service.register(email="ada@example.com", password=PASSWORD, name="Ada")

    [email] = mailer.sent
    assert (email.kind, email.to) == ("verification", "ada@example.com")
    assert email.token
    [stored] = uow.email_verification_tokens.tokens.values()
    assert email.token.encode() not in stored.token_hash  # only a digest is stored
    assert len(stored.token_hash) == 32


async def test_duplicate_of_a_verified_account_notifies_the_owner_only(
    service: AuthService, uow: FakeUnitOfWork, hasher: CountingHasher, mailer: RecordingMailer
) -> None:
    first = await service.register(email="ada@example.com", password=PASSWORD, name="Ada")
    assert first.user is not None
    await uow.users.mark_email_verified(first.user.id, first.user.created_at)

    second = await service.register(email="ADA@example.com ", password=PASSWORD + "!", name="Impostor")

    assert second.user is None
    assert len(uow.users.by_id) == 1
    assert [email.kind for email in mailer.sent] == ["verification", "account_exists"]
    assert mailer.sent[1].to == "ada@example.com"
    # The duplicate path still pays for a hash, so timing does not reveal the account.
    assert hasher.calls == 2


async def test_duplicate_of_an_unverified_account_resends_verification_after_cooldown(
    service: AuthService, mailer: RecordingMailer, clock: FakeClock
) -> None:
    await service.register(email="ada@example.com", password=PASSWORD, name="Ada")
    await service.register(email="ada@example.com", password=PASSWORD, name="Ada")  # within cooldown
    clock.advance(timedelta(minutes=2))
    await service.register(email="ada@example.com", password=PASSWORD, name="Ada")

    assert [email.kind for email in mailer.sent] == ["verification", "verification"]


async def test_duplicate_of_a_disabled_account_sends_nothing(
    service: AuthService, uow: FakeUnitOfWork, mailer: RecordingMailer
) -> None:
    first = await service.register(email="ada@example.com", password=PASSWORD, name="Ada")
    assert first.user is not None
    uow.users.by_id[first.user.id] = replace(first.user, status=UserStatus.DISABLED)
    mailer.sent.clear()

    await service.register(email="ada@example.com", password=PASSWORD, name="Ada")
    assert mailer.sent == []


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"email": "not-an-email"}, InvalidEmail),
        ({"name": "   "}, InvalidName),
        ({"password": "short"}, WeakPassword),
        ({"password": "ada@example.com is me"}, WeakPassword),
    ],
)
async def test_invalid_input_is_rejected_before_hashing_or_writing(
    service: AuthService,
    uow: FakeUnitOfWork,
    hasher: CountingHasher,
    mailer: RecordingMailer,
    fields: dict[str, str],
    error: type[Exception],
) -> None:
    request = {"email": "ada@example.com", "password": PASSWORD, "name": "Ada"} | fields
    with pytest.raises(error):
        await service.register(**request)
    assert hasher.calls == 0
    assert uow.users.by_id == {}
    assert mailer.sent == []
