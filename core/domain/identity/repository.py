import uuid
from datetime import datetime
from typing import Protocol

from .entities import DeletedUserValues, NewSession, NewUser, ProfileChanges, Session, SingleUseToken, User
from .enums import SessionRevocationReason


class UserRepository(Protocol):
    async def get(self, user_id: uuid.UUID) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None:
        """``email`` must already be normalized."""
        ...

    async def get_by_email_for_update(self, email: str) -> User | None:
        """Like get_by_email, but locks the row until the transaction ends, serializing
        concurrent token issuance for the same account."""
        ...

    async def mark_email_verified(self, user_id: uuid.UUID, at: datetime) -> None: ...

    async def update_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None: ...

    async def update_profile(self, user_id: uuid.UUID, changes: ProfileChanges) -> User: ...

    async def soft_delete(self, user_id: uuid.UUID, *, tombstone: DeletedUserValues, at: datetime) -> None:
        """Marks the account deleted and replaces its personal data in one UPDATE."""
        ...

    async def add(self, user: NewUser) -> User:
        """Raises EmailAlreadyRegistered on a duplicate; the surrounding transaction stays usable."""
        ...


class SingleUseTokenRepository(Protocol):
    """Storage for one kind of emailed token (email verification or password reset)."""

    async def add(
        self, *, user_id: uuid.UUID, token_hash: bytes, expires_at: datetime, created_at: datetime
    ) -> None: ...

    async def get_by_hash_for_update(self, token_hash: bytes) -> SingleUseToken | None:
        """Locks the row, so two requests presenting the same token cannot both consume it."""
        ...

    async def latest_outstanding_created_at(self, user_id: uuid.UUID) -> datetime | None: ...

    async def revoke_outstanding(self, user_id: uuid.UUID, at: datetime) -> None: ...

    async def mark_consumed(self, token_id: uuid.UUID, at: datetime) -> None: ...


class SessionRepository(Protocol):
    async def add(self, session: NewSession) -> Session: ...

    async def get(self, session_id: uuid.UUID) -> Session | None: ...

    async def get_for_update(self, session_id: uuid.UUID) -> Session | None:
        """Locks the row: concurrent refreshes of one session are serialized."""
        ...

    async def rotate(
        self, session_id: uuid.UUID, *, new_hash: bytes, previous_hash: bytes, at: datetime
    ) -> None: ...

    async def revoke(
        self, session_id: uuid.UUID, *, reason: SessionRevocationReason, at: datetime
    ) -> None: ...

    async def list_active(self, user_id: uuid.UUID, *, now: datetime, limit: int) -> list[Session]:
        """Unrevoked, unexpired sessions of one user, most recently used first."""
        ...

    async def revoke_owned(
        self, session_id: uuid.UUID, *, user_id: uuid.UUID, reason: SessionRevocationReason, at: datetime
    ) -> bool:
        """Revokes the session only if it is active and belongs to ``user_id`` (one scoped UPDATE).
        Returns whether anything was revoked."""
        ...

    async def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        *,
        reason: SessionRevocationReason,
        at: datetime,
        keep: uuid.UUID | None = None,
    ) -> int:
        """Revokes every active session of the user except ``keep``. Returns how many."""
        ...
