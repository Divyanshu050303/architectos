"""In-memory stand-ins for the persistence layer and mailer, for service tests without a database."""

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self

from core.domain.identity.entities import (
    DeletedUserValues,
    NewSession,
    NewUser,
    ProfileChanges,
    Session,
    SingleUseToken,
    User,
)
from core.domain.identity.enums import SessionRevocationReason, UserStatus
from core.domain.identity.errors import EmailAlreadyRegistered
from core.domain.organizations.entities import (
    Membership,
    MemberView,
    Organization,
    OrganizationWithRole,
    OwnedOrganization,
)
from core.domain.organizations.enums import Role


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

    async def update_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        self.by_id[user_id] = replace(self.by_id[user_id], password_hash=password_hash)

    async def update_profile(self, user_id: uuid.UUID, changes: ProfileChanges) -> User:
        user = self.by_id[user_id]
        if changes.name is not None:
            user = replace(user, name=changes.name)
        if not changes.keep_avatar:
            user = replace(user, avatar_url=changes.avatar_url)
        self.by_id[user_id] = replace(user, updated_at=self._clock())
        return self.by_id[user_id]

    async def soft_delete(self, user_id: uuid.UUID, *, tombstone: DeletedUserValues, at: datetime) -> None:
        self.by_id[user_id] = replace(
            self.by_id[user_id],
            status=UserStatus.DELETED,
            deleted_at=at,
            email=tombstone.email,
            name=tombstone.name,
            password_hash=tombstone.password_hash,
            avatar_url=None,
            email_verified_at=None,
        )

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


class FakeSessionRepository:
    def __init__(self) -> None:
        self.by_id: dict[uuid.UUID, Session] = {}

    async def add(self, session: NewSession) -> Session:
        stored = Session(
            id=uuid.uuid7(),
            user_id=session.user_id,
            refresh_token_hash=session.refresh_token_hash,
            previous_refresh_token_hash=None,
            refreshed_at=None,
            expires_at=session.expires_at,
            last_used_at=session.created_at,
            revoked_at=None,
            revoked_reason=None,
            user_agent=session.user_agent,
            ip_address=session.ip_address,
            created_at=session.created_at,
        )
        self.by_id[stored.id] = stored
        return stored

    async def get(self, session_id: uuid.UUID) -> Session | None:
        return self.by_id.get(session_id)

    async def get_for_update(self, session_id: uuid.UUID) -> Session | None:
        return self.by_id.get(session_id)

    async def rotate(
        self, session_id: uuid.UUID, *, new_hash: bytes, previous_hash: bytes, at: datetime
    ) -> None:
        self.by_id[session_id] = replace(
            self.by_id[session_id],
            refresh_token_hash=new_hash,
            previous_refresh_token_hash=previous_hash,
            refreshed_at=at,
            last_used_at=at,
        )

    async def revoke(self, session_id: uuid.UUID, *, reason: SessionRevocationReason, at: datetime) -> None:
        session = self.by_id[session_id]
        if session.revoked_at is None:
            self.by_id[session_id] = replace(session, revoked_at=at, revoked_reason=reason)

    async def list_active(self, user_id: uuid.UUID, *, now: datetime, limit: int) -> list[Session]:
        active = [s for s in self.by_id.values() if s.user_id == user_id and s.is_active(now)]
        active.sort(key=lambda s: (s.last_used_at or s.created_at, s.id), reverse=True)
        return active[:limit]

    async def revoke_owned(
        self, session_id: uuid.UUID, *, user_id: uuid.UUID, reason: SessionRevocationReason, at: datetime
    ) -> bool:
        session = self.by_id.get(session_id)
        if session is None or session.user_id != user_id or not session.is_active(at):
            return False
        await self.revoke(session_id, reason=reason, at=at)
        return True

    async def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        *,
        reason: SessionRevocationReason,
        at: datetime,
        keep: uuid.UUID | None = None,
    ) -> int:
        targets = [
            s for s in self.by_id.values() if s.user_id == user_id and s.revoked_at is None and s.id != keep
        ]
        for session in targets:
            await self.revoke(session.id, reason=reason, at=at)
        return len(targets)


class FakeOrganizationRepository:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.by_id: dict[uuid.UUID, Organization] = {}
        self.deleted: set[uuid.UUID] = set()

    async def add(self, *, name: str) -> Organization:
        now = self._clock()
        organization = Organization(id=uuid.uuid7(), name=name, created_at=now, updated_at=now)
        self.by_id[organization.id] = organization
        return organization

    async def rename(self, organization_id: uuid.UUID, name: str) -> Organization:
        self.by_id[organization_id] = replace(
            self.by_id[organization_id], name=name, updated_at=self._clock()
        )
        return self.by_id[organization_id]

    async def soft_delete(self, organization_id: uuid.UUID, at: datetime) -> None:
        self.deleted.add(organization_id)

    async def lock_active(self, organization_id: uuid.UUID) -> bool:
        return organization_id in self.by_id and organization_id not in self.deleted


class FakeMembershipRepository:
    def __init__(self, organizations: FakeOrganizationRepository, clock: FakeClock) -> None:
        self._organizations = organizations
        self._clock = clock
        self.by_id: dict[uuid.UUID, Membership] = {}

    async def add(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> Membership:
        membership = Membership(uuid.uuid7(), organization_id, user_id, role, self._clock())
        self.by_id[membership.id] = membership
        return membership

    def _live(self, membership: Membership) -> bool:
        return membership.organization_id not in self._organizations.deleted

    async def get_in_active_organization(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationWithRole | None:
        for m in self.by_id.values():
            if m.organization_id == organization_id and m.user_id == user_id and self._live(m):
                return OrganizationWithRole(self._organizations.by_id[organization_id], m)
        return None

    async def list_for_user(self, user_id: uuid.UUID) -> list[OrganizationWithRole]:
        found = [
            OrganizationWithRole(self._organizations.by_id[m.organization_id], m)
            for m in self.by_id.values()
            if m.user_id == user_id and self._live(m)
        ]
        return sorted(found, key=lambda o: o.organization.name.lower())

    def _in(self, organization_id: uuid.UUID) -> list[Membership]:
        return [m for m in self.by_id.values() if m.organization_id == organization_id]

    async def get_for_user(self, *, organization_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None:
        return next((m for m in self._in(organization_id) if m.user_id == user_id), None)

    async def get(self, *, organization_id: uuid.UUID, membership_id: uuid.UUID) -> Membership | None:
        found = self.by_id.get(membership_id)
        return found if found and found.organization_id == organization_id else None

    async def count_owners(self, organization_id: uuid.UUID) -> int:
        return sum(1 for m in self._in(organization_id) if m.role is Role.OWNER)

    async def list_members(self, organization_id: uuid.UUID) -> list[MemberView]:
        return [MemberView(m, "Someone", "someone@example.com", None) for m in self._in(organization_id)]

    async def update_role(self, membership_id: uuid.UUID, role: Role) -> Membership:
        self.by_id[membership_id] = replace(self.by_id[membership_id], role=role)
        return self.by_id[membership_id]

    async def delete(self, membership_id: uuid.UUID) -> None:
        del self.by_id[membership_id]

    async def owned_by(self, user_id: uuid.UUID) -> list[OwnedOrganization]:
        owned = {
            m.organization_id
            for m in self.by_id.values()
            if m.user_id == user_id and m.role is Role.OWNER and self._live(m)
        }
        return [
            OwnedOrganization(org, sum(1 for m in self._in(org) if m.role is Role.OWNER), len(self._in(org)))
            for org in sorted(owned)
        ]

    async def delete_all_for_user(self, user_id: uuid.UUID) -> None:
        for membership_id in [m.id for m in self.by_id.values() if m.user_id == user_id]:
            del self.by_id[membership_id]


class FakeUnitOfWork:
    def __init__(self, clock: FakeClock) -> None:
        self._users = FakeUserRepository(clock)
        self._email_verification_tokens = FakeTokenRepository()
        self._sessions = FakeSessionRepository()
        self._password_reset_tokens = FakeTokenRepository()
        self._organizations = FakeOrganizationRepository(clock)
        self._memberships = FakeMembershipRepository(self._organizations, clock)
        self.commits = 0
        self.rollbacks = 0

    @property
    def users(self) -> FakeUserRepository:
        return self._users

    @property
    def email_verification_tokens(self) -> FakeTokenRepository:
        return self._email_verification_tokens

    @property
    def sessions(self) -> FakeSessionRepository:
        return self._sessions

    @property
    def password_reset_tokens(self) -> FakeTokenRepository:
        return self._password_reset_tokens

    @property
    def organizations(self) -> FakeOrganizationRepository:
        return self._organizations

    @property
    def memberships(self) -> FakeMembershipRepository:
        return self._memberships

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

    async def send_password_reset(self, *, to: str, name: str, token: str) -> None:
        self.sent.append(SentEmail("password_reset", to, token))

    async def send_password_changed(self, *, to: str, name: str) -> None:
        self.sent.append(SentEmail("password_changed", to))
