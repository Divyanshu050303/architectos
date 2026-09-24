"""Password reset (forgotten password) and password change.

Both end the same way: new Argon2id hash, outstanding reset links revoked, sessions revoked,
and a "password changed" notice to the account's email. They differ in which sessions survive:
- reset: none. Whoever knew the old password (possibly an attacker) is signed out everywhere.
- change: the session making the change stays signed in; every other one is signed out.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.notifications import Mailer
from core.domain.unit_of_work import UnitOfWork

from .enums import SessionRevocationReason
from .errors import IncorrectPassword, InvalidEmail, InvalidToken, TokenExpired
from .passwords import PasswordHasher, PasswordPolicy
from .tokens import MAX_TOKEN_LENGTH, generate_token, hash_token
from .value_objects import normalize_email


@dataclass(frozen=True, slots=True)
class ResetSettings:
    ttl: timedelta = timedelta(minutes=30)
    # Minimum gap between two reset emails to the same account (anti email-bombing).
    cooldown: timedelta = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class _Notice:
    to: str
    name: str


@dataclass(frozen=True, slots=True)
class _ResetLink:
    to: str
    name: str
    token: str


class PasswordService:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        hasher: PasswordHasher,
        policy: PasswordPolicy,
        mailer: Mailer,
        settings: ResetSettings,
        clock: Clock = utc_now,
    ) -> None:
        self._uow = uow
        self._hasher = hasher
        self._policy = policy
        self._mailer = mailer
        self._settings = settings
        self._clock = clock

    async def request_reset(self, *, email: str) -> None:
        """Always succeeds from the caller's point of view: unknown, invalid, disabled or throttled
        requests silently send nothing, so the endpoint never reveals whether an account exists."""
        try:
            normalized_email = normalize_email(email)
        except InvalidEmail:
            return
        now = self._clock()
        async with self._uow as uow:
            user = await uow.users.get_by_email_for_update(normalized_email)
            if user is None or not user.can_sign_in:
                return
            tokens = uow.password_reset_tokens
            latest = await tokens.latest_outstanding_created_at(user.id)
            if latest is not None and now - latest < self._settings.cooldown:
                return
            token = generate_token()
            await tokens.revoke_outstanding(user.id, now)
            await tokens.add(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=now + self._settings.ttl,
                created_at=now,
            )
            link = _ResetLink(user.email, user.name, token)
        await self._mailer.send_password_reset(to=link.to, name=link.name, token=link.token)

    async def reset(self, *, token: str, new_password: str) -> None:
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise InvalidToken
        now = self._clock()
        async with self._uow as uow:
            stored = await uow.password_reset_tokens.get_by_hash_for_update(hash_token(token))
            if stored is None or stored.is_spent:
                raise InvalidToken
            if stored.expires_at <= now:
                raise TokenExpired
            user = await uow.users.get(stored.user_id)
            if user is None or not user.can_sign_in:
                raise InvalidToken
            # A weak password raises here, before the token is consumed: the link stays usable.
            self._policy.validate(new_password, email=user.email)
            new_hash = await self._hasher.hash_async(new_password)

            await uow.users.update_password_hash(user.id, new_hash)
            await uow.password_reset_tokens.mark_consumed(stored.id, now)
            await uow.password_reset_tokens.revoke_outstanding(user.id, now)
            revoked = await uow.sessions.revoke_all_for_user(
                user.id, reason=SessionRevocationReason.PASSWORD_RESET, at=now
            )
            await uow.audit.record(
                AuditEvent(
                    AuditAction.USER_PASSWORD_RESET,
                    actor_user_id=user.id,
                    resource_type="user",
                    resource_id=user.id,
                    metadata={"sessionsRevoked": revoked},
                )
            )
            if not user.is_email_verified:
                # Following the emailed link proves control of the inbox.
                await uow.users.mark_email_verified(user.id, now)
            notice = _Notice(user.email, user.name)
        await self._mailer.send_password_changed(to=notice.to, name=notice.name)

    async def change(
        self, *, user_id: uuid.UUID, current_session_id: uuid.UUID, current_password: str, new_password: str
    ) -> None:
        now = self._clock()
        async with self._uow as uow:
            user = await uow.users.get(user_id)
            if user is None or not user.can_sign_in:
                raise IncorrectPassword
            matches = await self._hasher.verify_async(user.password_hash, current_password)
            if not matches:
                raise IncorrectPassword
            self._policy.validate(new_password, email=user.email)
            new_hash = await self._hasher.hash_async(new_password)

            await uow.users.update_password_hash(user.id, new_hash)
            await uow.password_reset_tokens.revoke_outstanding(user.id, now)
            revoked = await uow.sessions.revoke_all_for_user(
                user.id, reason=SessionRevocationReason.PASSWORD_CHANGED, at=now, keep=current_session_id
            )
            await uow.audit.record(
                AuditEvent(
                    AuditAction.USER_PASSWORD_CHANGED,
                    actor_user_id=user.id,
                    resource_type="user",
                    resource_id=user.id,
                    metadata={"sessionsRevoked": revoked},
                )
            )
            notice = _Notice(user.email, user.name)
        await self._mailer.send_password_changed(to=notice.to, name=notice.name)
