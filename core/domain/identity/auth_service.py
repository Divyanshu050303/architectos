"""Authentication use cases: registration and email verification."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.notifications import Mailer
from core.domain.unit_of_work import UnitOfWork

from .entities import NewUser, User
from .errors import EmailAlreadyRegistered, InvalidEmail, InvalidToken, TokenExpired
from .passwords import PasswordHasher, PasswordPolicy
from .tokens import MAX_TOKEN_LENGTH, generate_token, hash_token
from .value_objects import normalize_email, normalize_name


@dataclass(frozen=True, slots=True)
class VerificationSettings:
    ttl: timedelta = timedelta(hours=24)
    # Minimum gap between two verification emails to the same account (anti email-bombing).
    resend_cooldown: timedelta = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """``user`` is None when the email was already registered. Callers must answer both cases
    identically, so registration cannot be used to discover which emails have accounts."""

    user: User | None


# Emails are decided on inside a transaction and sent only after it commits.
@dataclass(frozen=True, slots=True)
class _VerificationEmail:
    to: str
    name: str
    token: str


@dataclass(frozen=True, slots=True)
class _AccountExistsEmail:
    to: str
    name: str


type _Outgoing = _VerificationEmail | _AccountExistsEmail | None


class AuthService:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        hasher: PasswordHasher,
        policy: PasswordPolicy,
        mailer: Mailer,
        verification: VerificationSettings,
        clock: Clock = utc_now,
    ) -> None:
        self._uow = uow
        self._hasher = hasher
        self._policy = policy
        self._mailer = mailer
        self._verification = verification
        self._clock = clock

    # --- registration -----------------------------------------------------------------------

    async def register(self, *, email: str, password: str, name: str) -> RegistrationResult:
        normalized_email = normalize_email(email)
        clean_name = normalize_name(name)
        self._policy.validate(password, email=normalized_email)
        # Always hash, including for an email that turns out to be taken: the response time
        # must not reveal which case happened.
        password_hash = await asyncio.to_thread(self._hasher.hash, password)

        created: User | None = None
        async with self._uow as uow:
            try:
                created = await uow.users.add(
                    NewUser(email=normalized_email, name=clean_name, password_hash=password_hash)
                )
                await uow.audit.record(
                    AuditEvent(
                        AuditAction.USER_REGISTERED,
                        actor_user_id=created.id,
                        resource_type="user",
                        resource_id=created.id,
                    )
                )
                outgoing = await self._issue_verification(uow, created)
            except EmailAlreadyRegistered:
                existing = await uow.users.get_by_email_for_update(normalized_email)
                outgoing = await self._email_for_existing_account(uow, existing)

        await self._send(outgoing)
        return RegistrationResult(user=created)

    async def _email_for_existing_account(self, uow: UnitOfWork, existing: User | None) -> _Outgoing:
        # The real owner hears about the attempt; the requester learns nothing.
        if existing is None or not existing.can_sign_in:
            return None
        if existing.is_email_verified:
            return _AccountExistsEmail(existing.email, existing.name)
        return await self._issue_verification(uow, existing)

    # --- email verification -----------------------------------------------------------------

    async def resend_verification(self, *, email: str) -> None:
        """Always succeeds from the caller's point of view: unknown, verified or throttled
        accounts silently get nothing, so the endpoint does not reveal account state."""
        try:
            normalized_email = normalize_email(email)
        except InvalidEmail:
            return
        async with self._uow as uow:
            user = await uow.users.get_by_email_for_update(normalized_email)
            if user is None or not user.can_sign_in or user.is_email_verified:
                return
            outgoing = await self._issue_verification(uow, user)
        await self._send(outgoing)

    async def verify_email(self, *, token: str) -> None:
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise InvalidToken
        now = self._clock()
        async with self._uow as uow:
            stored = await uow.email_verification_tokens.get_by_hash_for_update(hash_token(token))
            if stored is None or stored.is_spent:
                raise InvalidToken
            if stored.expires_at <= now:
                raise TokenExpired
            user = await uow.users.get(stored.user_id)
            if user is None or not user.can_sign_in:
                raise InvalidToken
            await uow.email_verification_tokens.mark_consumed(stored.id, now)
            # Any other outstanding link for this account is now pointless.
            await uow.email_verification_tokens.revoke_outstanding(user.id, now)
            if not user.is_email_verified:
                await uow.users.mark_email_verified(user.id, now)
                await uow.audit.record(
                    AuditEvent(
                        AuditAction.USER_EMAIL_VERIFIED,
                        actor_user_id=user.id,
                        resource_type="user",
                        resource_id=user.id,
                    )
                )

    async def _issue_verification(self, uow: UnitOfWork, user: User) -> _Outgoing:
        """Replaces any outstanding verification token with a new one, unless the last was
        issued within the cooldown. Caller holds the user row lock (or just created it)."""
        now = self._clock()
        tokens = uow.email_verification_tokens
        latest = await tokens.latest_outstanding_created_at(user.id)
        if latest is not None and now - latest < self._verification.resend_cooldown:
            return None
        token = generate_token()
        await tokens.revoke_outstanding(user.id, now)
        await tokens.add(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=now + self._verification.ttl,
            created_at=now,
        )
        return _VerificationEmail(user.email, user.name, token)

    async def _send(self, email: _Outgoing) -> None:
        match email:
            case _VerificationEmail(to=to, name=name, token=token):
                await self._mailer.send_email_verification(to=to, name=name, token=token)
            case _AccountExistsEmail(to=to, name=name):
                await self._mailer.send_account_exists(to=to, name=name)
            case None:
                pass
