"""Transaction boundary for domain services.

A service opens ``async with uow:``; everything done through ``uow``'s repositories inside the
block commits together when it exits normally and rolls back if it raises. Repositories never
commit on their own. This keeps SQLAlchemy out of the domain while services own transactions.
"""

from types import TracebackType
from typing import Protocol, Self

from core.domain.audit.repository import AuditRepository
from core.domain.identity.repository import SessionRepository, SingleUseTokenRepository, UserRepository
from core.domain.organizations.repository import (
    InvitationRepository,
    MembershipRepository,
    OrganizationRepository,
)
from core.domain.projects.repository import ProjectRepository
from core.domain.requirements.repository import RequirementRepository, RequirementSetRepository


class UnitOfWork(Protocol):
    @property
    def users(self) -> UserRepository: ...

    @property
    def email_verification_tokens(self) -> SingleUseTokenRepository: ...

    @property
    def sessions(self) -> SessionRepository: ...

    @property
    def password_reset_tokens(self) -> SingleUseTokenRepository: ...

    @property
    def organizations(self) -> OrganizationRepository: ...

    @property
    def memberships(self) -> MembershipRepository: ...

    @property
    def invitations(self) -> InvitationRepository: ...

    @property
    def audit(self) -> AuditRepository: ...

    @property
    def projects(self) -> ProjectRepository: ...

    @property
    def requirements(self) -> RequirementRepository: ...

    @property
    def requirement_sets(self) -> RequirementSetRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None: ...
