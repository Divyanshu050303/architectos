from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from core.domain.client import ClientInfo
from persistence.models import EmailVerificationTokenRecord, PasswordResetTokenRecord
from persistence.repositories.architectures import SqlAlchemyArchitectureRepository
from persistence.repositories.audit_logs import SqlAlchemyAuditRepository
from persistence.repositories.capacity import SqlAlchemyCapacityAnalysisRepository
from persistence.repositories.cost import SqlAlchemyCostAnalysisRepository
from persistence.repositories.invitations import SqlAlchemyInvitationRepository
from persistence.repositories.organizations import (
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
)
from persistence.repositories.pricing import SqlAlchemyPricingSnapshotRepository
from persistence.repositories.projects import SqlAlchemyProjectRepository
from persistence.repositories.reliability import SqlAlchemyReliabilityAnalysisRepository
from persistence.repositories.requirement_analyses import SqlAlchemyRequirementAnalysisRepository
from persistence.repositories.requirement_sets import SqlAlchemyRequirementSetRepository
from persistence.repositories.requirements import SqlAlchemyRequirementRepository
from persistence.repositories.sessions import SqlAlchemySessionRepository
from persistence.repositories.single_use_tokens import SqlAlchemySingleUseTokenRepository
from persistence.repositories.users import SqlAlchemyUserRepository
from persistence.repositories.validations import SqlAlchemyValidationRunRepository


class SqlAlchemyUnitOfWork:
    """Implements core.domain.unit_of_work.UnitOfWork over one AsyncSession.

    The session must not be in a transaction already: each ``async with`` is one transaction.
    """

    def __init__(self, session: AsyncSession, client: ClientInfo | None = None) -> None:
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
        self.invitations = SqlAlchemyInvitationRepository(session)
        self.audit = SqlAlchemyAuditRepository(session, client or ClientInfo())
        self.projects = SqlAlchemyProjectRepository(session)
        self.requirements = SqlAlchemyRequirementRepository(session)
        self.requirement_sets = SqlAlchemyRequirementSetRepository(session)
        self.requirement_analyses = SqlAlchemyRequirementAnalysisRepository(session)
        self.architectures = SqlAlchemyArchitectureRepository(session)
        self.validations = SqlAlchemyValidationRunRepository(session)
        self.capacity = SqlAlchemyCapacityAnalysisRepository(session)
        self.pricing = SqlAlchemyPricingSnapshotRepository(session)
        self.cost = SqlAlchemyCostAnalysisRepository(session)
        self.reliability = SqlAlchemyReliabilityAnalysisRepository(session)

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
            self.audit.publish_committed()
        else:
            await transaction.rollback()
            self.audit.discard_uncommitted()
