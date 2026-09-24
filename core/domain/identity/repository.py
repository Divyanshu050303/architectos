import uuid
from datetime import datetime
from typing import Protocol

from .entities import NewUser, SingleUseToken, User


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
