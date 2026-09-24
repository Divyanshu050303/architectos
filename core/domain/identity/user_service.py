"""Profile management and account deletion.

Deletion is soft, with personal data scrubbed in the same transaction:
- status "deleted" and deleted_at set; the row stays, so audit entries and invitations that
  reference the account keep resolving;
- email replaced with an undeliverable tombstone (the address can be registered again), name
  replaced, avatar removed, password hash replaced with a value no password verifies against;
- every session and every outstanding verification or reset link revoked.
Organizations: deletion is refused while the user is the only owner of an organization with other
members; organizations the user is alone in are soft-deleted; other memberships are removed.
"""

import asyncio
import uuid

from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.organizations.membership_service import release_memberships
from core.domain.unit_of_work import UnitOfWork

from .entities import DeletedUserValues, ProfileChanges, User
from .enums import SessionRevocationReason
from .errors import IncorrectPassword, NothingToUpdate, SessionRevoked
from .passwords import PasswordHasher
from .value_objects import DELETED_USER_NAME, normalize_avatar_url, normalize_name, tombstone_email

# Not a valid PHC string: Argon2 verification of any password against it fails.
UNUSABLE_PASSWORD_HASH = "!deleted"  # noqa: S105 — a sentinel that can never verify, not a secret


class UserService:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        hasher: PasswordHasher,
        avatar_hosts: frozenset[str] = frozenset(),
        clock: Clock = utc_now,
    ) -> None:
        self._uow = uow
        self._hasher = hasher
        self._avatar_hosts = avatar_hosts
        self._clock = clock

    async def update_profile(
        self,
        *,
        user_id: uuid.UUID,
        name: str | None = None,
        avatar_url: str | None = None,
        set_avatar: bool = False,
    ) -> User:
        """``set_avatar`` says whether avatar_url was supplied at all; None with set_avatar removes it."""
        if name is None and not set_avatar:
            raise NothingToUpdate
        changes = ProfileChanges(
            name=normalize_name(name) if name is not None else None,
            avatar_url=(
                normalize_avatar_url(avatar_url, allowed_hosts=self._avatar_hosts)
                if avatar_url is not None
                else None
            ),
            keep_avatar=not set_avatar,
        )
        fields = [
            name for name, changed in (("name", name is not None), ("avatarUrl", set_avatar)) if changed
        ]
        async with self._uow as uow:
            updated = await uow.users.update_profile(user_id, changes)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.USER_PROFILE_UPDATED,
                    actor_user_id=user_id,
                    resource_type="user",
                    resource_id=user_id,
                    metadata={"fields": fields},
                )
            )
            return updated

    async def delete_account(self, *, user_id: uuid.UUID, password: str) -> None:
        now = self._clock()
        async with self._uow as uow:
            user = await uow.users.get(user_id)
            if user is None or not user.can_sign_in:
                raise SessionRevoked
            matches = await asyncio.to_thread(self._hasher.verify, user.password_hash, password)
            if not matches:
                raise IncorrectPassword
            # First, so that being the sole owner of a shared organization aborts everything.
            await release_memberships(uow, user_id=user.id, at=now)
            await uow.users.soft_delete(
                user.id,
                tombstone=DeletedUserValues(
                    email=tombstone_email(user.id),
                    name=DELETED_USER_NAME,
                    password_hash=UNUSABLE_PASSWORD_HASH,
                ),
                at=now,
            )
            await uow.sessions.revoke_all_for_user(
                user.id, reason=SessionRevocationReason.ACCOUNT_DELETED, at=now
            )
            await uow.email_verification_tokens.revoke_outstanding(user.id, now)
            await uow.password_reset_tokens.revoke_outstanding(user.id, now)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.USER_DELETED, actor_user_id=user.id, resource_type="user", resource_id=user.id
                )
            )
