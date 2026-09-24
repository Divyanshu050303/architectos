"""Sign-in and sessions: login, refresh-token rotation with reuse detection, and access checks.

Refresh strategy (see docs/security/authentication.md):
- Each refresh replaces the session's secret and remembers the previous hash.
- Presenting the previous token within ``reuse_grace`` of its rotation is a benign race
  (two tabs refreshing at once): RefreshConflict, nothing revoked.
- Presenting it later means two parties hold the token family: the session is revoked.
- A secret matching neither hash is rejected without revoking, so knowing a session id alone
  cannot be used to sign someone out.
Sessions expire ``refresh_ttl`` after login; refreshing does not extend them.
"""

import asyncio
import hmac
import uuid
from dataclasses import dataclass
from datetime import timedelta

from core.domain.clock import Clock, utc_now
from core.domain.unit_of_work import UnitOfWork

from .entities import NewSession, Session, User
from .enums import SessionRevocationReason
from .errors import (
    AccountDisabled,
    InvalidCredentials,
    InvalidEmail,
    InvalidRefreshToken,
    RefreshConflict,
    SessionExpired,
    SessionRevoked,
)
from .passwords import PasswordHasher
from .tokens import format_refresh_token, generate_token, hash_token, parse_refresh_token
from .value_objects import normalize_email

MAX_USER_AGENT_LENGTH = 512


@dataclass(frozen=True, slots=True)
class SessionSettings:
    refresh_ttl: timedelta = timedelta(days=30)
    reuse_grace: timedelta = timedelta(seconds=10)


@dataclass(frozen=True, slots=True)
class ClientInfo:
    user_agent: str | None = None
    ip_address: str | None = None


@dataclass(frozen=True, slots=True)
class SignedIn:
    """``refresh_token`` is the raw secret for the client; it is stored only as a hash."""

    user: User
    session: Session
    refresh_token: str


class SessionService:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        hasher: PasswordHasher,
        settings: SessionSettings,
        clock: Clock = utc_now,
    ) -> None:
        self._uow = uow
        self._hasher = hasher
        self._settings = settings
        self._clock = clock

    async def login(self, *, email: str, password: str, client: ClientInfo) -> SignedIn:
        try:
            normalized_email = normalize_email(email)
        except InvalidEmail:
            normalized_email = None

        async with self._uow as uow:
            user = await uow.users.get_by_email(normalized_email) if normalized_email else None
            password_hash = user.password_hash if user else self._hasher.dummy_hash
            matches = await asyncio.to_thread(self._hasher.verify, password_hash, password)
            if user is None or not matches:
                raise InvalidCredentials
            if not user.can_sign_in:
                raise AccountDisabled
            if self._hasher.needs_rehash(user.password_hash):
                # Parameters were strengthened since this hash was made; upgrade it transparently.
                new_hash = await asyncio.to_thread(self._hasher.hash, password)
                await uow.users.update_password_hash(user.id, new_hash)

            now = self._clock()
            secret = generate_token()
            session = await uow.sessions.add(
                NewSession(
                    user_id=user.id,
                    refresh_token_hash=hash_token(secret),
                    expires_at=now + self._settings.refresh_ttl,
                    created_at=now,
                    user_agent=(client.user_agent or None) and client.user_agent[:MAX_USER_AGENT_LENGTH],
                    ip_address=client.ip_address,
                )
            )
        return SignedIn(user=user, session=session, refresh_token=format_refresh_token(session.id, secret))

    async def refresh(self, *, refresh_token: str) -> SignedIn:
        parsed = parse_refresh_token(refresh_token)
        if parsed is None:
            raise InvalidRefreshToken
        session_id, secret = parsed
        presented = hash_token(secret)
        now = self._clock()

        async with self._uow as uow:
            session = await uow.sessions.get_for_update(session_id)
            if session is None or session.revoked_at is not None:
                raise InvalidRefreshToken
            if now >= session.expires_at:
                raise SessionExpired

            if hmac.compare_digest(presented, session.refresh_token_hash):
                user = await uow.users.get(session.user_id)
                if user is None or not user.can_sign_in:
                    raise InvalidRefreshToken
                new_secret = generate_token()
                await uow.sessions.rotate(
                    session.id, new_hash=hash_token(new_secret), previous_hash=presented, at=now
                )
                rotated = await uow.sessions.get(session.id)
                assert rotated is not None  # noqa: S101 — updated inside this transaction
                return SignedIn(
                    user=user, session=rotated, refresh_token=format_refresh_token(session.id, new_secret)
                )

            previous = session.previous_refresh_token_hash
            if previous is None or not hmac.compare_digest(presented, previous):
                raise InvalidRefreshToken
            if session.refreshed_at is not None and now - session.refreshed_at <= self._settings.reuse_grace:
                raise RefreshConflict
            # A rotated-out token came back after the grace window: two parties hold this session.
            await uow.sessions.revoke(session.id, reason=SessionRevocationReason.TOKEN_REUSE, at=now)
        # Raised outside the block so the revocation is committed rather than rolled back.
        raise InvalidRefreshToken

    async def authenticate(self, *, session_id: uuid.UUID, user_id: uuid.UUID) -> tuple[User, Session]:
        """Checks the session behind an access token is still live. One indexed read per request."""
        now = self._clock()
        async with self._uow as uow:
            session = await uow.sessions.get(session_id)
            if session is None or session.user_id != user_id or not session.is_active(now):
                raise SessionRevoked
            user = await uow.users.get(user_id)
            if user is None or not user.can_sign_in:
                raise SessionRevoked
        return user, session
