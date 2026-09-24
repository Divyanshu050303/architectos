import pytest

from core.domain.identity.auth_service import AuthService, VerificationSettings
from core.domain.identity.passwords import PasswordHasher, PasswordPolicy

from .fakes import FakeClock, FakeUnitOfWork, RecordingMailer


class CountingHasher(PasswordHasher):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def hash(self, password: str) -> str:
        self.calls += 1
        return super().hash(password)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def uow(clock: FakeClock) -> FakeUnitOfWork:
    return FakeUnitOfWork(clock)


@pytest.fixture
def hasher() -> CountingHasher:
    return CountingHasher()


@pytest.fixture
def mailer() -> RecordingMailer:
    return RecordingMailer()


@pytest.fixture
def service(
    uow: FakeUnitOfWork, hasher: CountingHasher, mailer: RecordingMailer, clock: FakeClock
) -> AuthService:
    return AuthService(
        uow,
        hasher=hasher,
        policy=PasswordPolicy(min_length=12),
        mailer=mailer,
        verification=VerificationSettings(),
        clock=clock,
    )
