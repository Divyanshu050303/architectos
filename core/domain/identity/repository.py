import uuid
from typing import Protocol

from .entities import NewUser, User


class UserRepository(Protocol):
    async def get(self, user_id: uuid.UUID) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None:
        """``email`` must already be normalized."""
        ...

    async def add(self, user: NewUser) -> User:
        """Raises EmailAlreadyRegistered on a duplicate; the surrounding transaction stays usable."""
        ...
