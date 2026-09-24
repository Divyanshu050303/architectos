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

from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.client import ClientInfo
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
    SessionNotFound,
    SessionRevoked,
)
from .passwords import PasswordHasher
from .tokens import format_refresh_token, generate_token, hash_token, parse_refresh_token
from .value_objects import normalize_email

MAX_USER_AGENT_LENGTH = 512
# Upper bound for GET /me/sessions; each sign-in creates a session and expired ones are skipped.
MAX_LISTED_SESSIONS = 100


@dataclass(frozen=True, slots=True)
class SessionSettings:
    refresh_ttl: timedelta = timedelta(days=30)
    reuse_grace: timedelta = timedelta(seconds=10)


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

        signed_in: SignedIn | None = None
        refusal: type[InvalidCredentials | AccountDisabled] = InvalidCredentials
        async with self._uow as uow:
            user = await uow.users.get_by_email(normalized_email) if normalized_email else None
            password_hash = user.password_hash if user else self._hasher.dummy_hash
            matches = await asyncio.to_thread(self._hasher.verify, password_hash, password)
            if user is not None and (not matches or not user.can_sign_in):
                # Failed attempts on real accounts are audited; the entry commits with this block
                # and the refusal is raised after it. Unknown emails are not recorded.
                refusal = InvalidCredentials if not matches else AccountDisabled
                await uow.audit.record(
                    AuditEvent(
                        AuditAction.USER_LOGIN_FAILED,
                        actor_user_id=None,
                        resource_type="user",
                        resource_id=user.id,
                        metadata={"reason": "wrong_password" if not matches else "account_disabled"},
                    )
                )
            elif user is not None:
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
                await uow.audit.record(
                    AuditEvent(
                        AuditAction.USER_LOGIN,
                        actor_user_id=user.id,
                        resource_type="session",
                        resource_id=session.id,
                    )
                )
                signed_in = SignedIn(
                    user=user, session=session, refresh_token=format_refresh_token(session.id, secret)
                )
        if signed_in is None:
            raise refusal
        return signed_in

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
            await uow.audit.record(
                AuditEvent(
                    AuditAction.SESSION_REVOKED,
                    actor_user_id=None,
                    resource_type="session",
                    resource_id=session.id,
                    metadata={
                        "reason": SessionRevocationReason.TOKEN_REUSE.value,
                        "userId": str(session.user_id),
                    },
                )
            )
        # Raised outside the block so the revocation is committed rather than rolled back.
        raise InvalidRefreshToken

    async def logout(self, *, refresh_token: str | None) -> None:
        """Revokes the session behind a refresh cookie. Idempotent: a missing, malformed, forged or
        already-revoked token is a no-op, so logging out twice (or with a stale cookie) succeeds.
        The secret must match the current or previous hash: a session id alone cannot sign anyone out."""
        parsed = parse_refresh_token(refresh_token) if refresh_token else None
        if parsed is None:
            return
        session_id, secret = parsed
        presented = hash_token(secret)
        async with self._uow as uow:
            session = await uow.sessions.get_for_update(session_id)
            if session is None or session.revoked_at is not None:
                return
            known = [session.refresh_token_hash, session.previous_refresh_token_hash]
            if any(h is not None and hmac.compare_digest(presented, h) for h in known):
                await uow.sessions.revoke(session.id, reason=SessionRevocationReason.LOGOUT, at=self._clock())
                await uow.audit.record(
                    AuditEvent(
                        AuditAction.USER_LOGOUT,
                        actor_user_id=session.user_id,
                        resource_type="session",
                        resource_id=session.id,
                    )
                )

    async def list_sessions(self, *, user_id: uuid.UUID) -> list[Session]:
        async with self._uow as uow:
            return await uow.sessions.list_active(user_id, now=self._clock(), limit=MAX_LISTED_SESSIONS)

    async def revoke_session(self, *, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        async with self._uow as uow:
            revoked = await uow.sessions.revoke_owned(
                session_id, user_id=user_id, reason=SessionRevocationReason.USER_REVOKED, at=self._clock()
            )
            if not revoked:
                raise SessionNotFound
            await uow.audit.record(
                AuditEvent(
                    AuditAction.SESSION_REVOKED,
                    actor_user_id=user_id,
                    resource_type="session",
                    resource_id=session_id,
                    metadata={"reason": SessionRevocationReason.USER_REVOKED.value},
                )
            )

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
