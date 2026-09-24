"""In-memory stand-ins for the persistence layer and mailer, for service tests without a database."""

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self

from core.domain.identity.entities import NewUser, SingleUseToken, User
from core.domain.identity.enums import UserStatus
from core.domain.identity.errors import EmailAlreadyRegistered


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeUserRepository:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.by_id: dict[uuid.UUID, User] = {}

    async def get(self, user_id: uuid.UUID) -> User | None:
        return self.by_id.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        return next((user for user in self.by_id.values() if user.email.lower() == email), None)

    async def get_by_email_for_update(self, email: str) -> User | None:
        return await self.get_by_email(email)

    async def mark_email_verified(self, user_id: uuid.UUID, at: datetime) -> None:
        user = self.by_id[user_id]
        if user.email_verified_at is None:
            self.by_id[user_id] = replace(user, email_verified_at=at)

    async def add(self, user: NewUser) -> User:
        if await self.get_by_email(user.email.lower()):
            raise EmailAlreadyRegistered
        now = self._clock()
        stored = User(
            id=uuid.uuid7(),
            email=user.email,
            name=user.name,
            password_hash=user.password_hash,
            avatar_url=None,
            email_verified_at=None,
            status=UserStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self.by_id[stored.id] = stored
        return stored


class FakeTokenRepository:
    def __init__(self) -> None:
        self.tokens: dict[uuid.UUID, SingleUseToken] = {}

    async def add(
        self, *, user_id: uuid.UUID, token_hash: bytes, expires_at: datetime, created_at: datetime
    ) -> None:
        token = SingleUseToken(uuid.uuid7(), user_id, token_hash, expires_at, None, None, created_at)
        self.tokens[token.id] = token

    async def get_by_hash_for_update(self, token_hash: bytes) -> SingleUseToken | None:
        return next((token for token in self.tokens.values() if token.token_hash == token_hash), None)

    def _outstanding(self, user_id: uuid.UUID) -> list[SingleUseToken]:
        return [t for t in self.tokens.values() if t.user_id == user_id and not t.is_spent]

    async def latest_outstanding_created_at(self, user_id: uuid.UUID) -> datetime | None:
        return max((t.created_at for t in self._outstanding(user_id)), default=None)

    async def revoke_outstanding(self, user_id: uuid.UUID, at: datetime) -> None:
        for token in self._outstanding(user_id):
            self.tokens[token.id] = replace(token, revoked_at=at)

    async def mark_consumed(self, token_id: uuid.UUID, at: datetime) -> None:
        self.tokens[token_id] = replace(self.tokens[token_id], consumed_at=at)


class FakeUnitOfWork:
    def __init__(self, clock: FakeClock) -> None:
        self._users = FakeUserRepository(clock)
        self._email_verification_tokens = FakeTokenRepository()
        self.commits = 0
        self.rollbacks = 0

    @property
    def users(self) -> FakeUserRepository:
        return self._users

    @property
    def email_verification_tokens(self) -> FakeTokenRepository:
        return self._email_verification_tokens

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if exc_type is None:
            self.commits += 1
        else:
            self.rollbacks += 1


@dataclass(frozen=True)
class SentEmail:
    kind: str
    to: str
    token: str | None = None


class RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[SentEmail] = []

    async def send_email_verification(self, *, to: str, name: str, token: str) -> None:
        self.sent.append(SentEmail("verification", to, token))

    async def send_account_exists(self, *, to: str, name: str) -> None:
        self.sent.append(SentEmail("account_exists", to))
