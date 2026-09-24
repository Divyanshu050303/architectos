import pytest

from core.domain.identity.auth_service import AuthService
from core.domain.identity.errors import InvalidEmail, InvalidName, WeakPassword
from core.domain.identity.passwords import PasswordHasher, PasswordPolicy

from .fakes import FakeUnitOfWork

PASSWORD = "correct horse battery staple"


class CountingHasher(PasswordHasher):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def hash(self, password: str) -> str:
        self.calls += 1
        return super().hash(password)


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def hasher() -> CountingHasher:
    return CountingHasher()


@pytest.fixture
def service(uow: FakeUnitOfWork, hasher: CountingHasher) -> AuthService:
    return AuthService(uow, hasher=hasher, policy=PasswordPolicy(min_length=12))


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


async def test_duplicate_email_is_not_an_error_and_creates_nothing(
    service: AuthService, uow: FakeUnitOfWork, hasher: CountingHasher
) -> None:
    await service.register(email="ada@example.com", password=PASSWORD, name="Ada")
    second = await service.register(email="ADA@example.com ", password=PASSWORD + "!", name="Impostor")

    assert second.user is None
    assert len(uow.users.by_id) == 1
    # The duplicate path still pays for a hash, so timing does not reveal the account.
    assert hasher.calls == 2


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
    fields: dict[str, str],
    error: type[Exception],
) -> None:
    request = {"email": "ada@example.com", "password": PASSWORD, "name": "Ada"} | fields
    with pytest.raises(error):
        await service.register(**request)
    assert hasher.calls == 0
    assert uow.users.by_id == {}
