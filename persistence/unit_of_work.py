from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from persistence.models import EmailVerificationTokenRecord, PasswordResetTokenRecord
from persistence.repositories.organizations import (
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
)
from persistence.repositories.sessions import SqlAlchemySessionRepository
from persistence.repositories.single_use_tokens import SqlAlchemySingleUseTokenRepository
from persistence.repositories.users import SqlAlchemyUserRepository


class SqlAlchemyUnitOfWork:
    """Implements core.domain.unit_of_work.UnitOfWork over one AsyncSession.

    The session must not be in a transaction already: each ``async with`` is one transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._transaction: AsyncSessionTransaction | None = None
        self.users = SqlAlchemyUserRepository(session)
        self.email_verification_tokens = SqlAlchemySingleUseTokenRepository(
            session, EmailVerificationTokenRecord
        )
        self.sessions = SqlAlchemySessionRepository(session)
        self.password_reset_tokens = SqlAlchemySingleUseTokenRepository(session, PasswordResetTokenRecord)
        self.organizations = SqlAlchemyOrganizationRepository(session)
        self.memberships = SqlAlchemyMembershipRepository(session)

    async def __aenter__(self) -> Self:
        self._transaction = await self._session.begin()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        transaction, self._transaction = self._transaction, None
        if transaction is None:
            return
        if exc_type is None:
            await transaction.commit()
        else:
            await transaction.rollback()
