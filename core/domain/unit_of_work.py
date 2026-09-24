"""Transaction boundary for domain services.

A service opens ``async with uow:``; everything done through ``uow``'s repositories inside the
block commits together when it exits normally and rolls back if it raises. Repositories never
commit on their own. This keeps SQLAlchemy out of the domain while services own transactions.
"""

from types import TracebackType
from typing import Protocol, Self

from core.domain.identity.repository import SessionRepository, SingleUseTokenRepository, UserRepository


class UnitOfWork(Protocol):
    @property
    def users(self) -> UserRepository: ...

    @property
    def email_verification_tokens(self) -> SingleUseTokenRepository: ...

    @property
    def sessions(self) -> SessionRepository: ...

    @property
    def password_reset_tokens(self) -> SingleUseTokenRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None: ...
