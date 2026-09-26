"""Transaction boundary for domain services.

A service opens ``async with uow:``; everything done through ``uow``'s repositories inside the
block commits together when it exits normally and rolls back if it raises. Repositories never
commit on their own. This keeps SQLAlchemy out of the domain while services own transactions.
"""

from types import TracebackType
from typing import Protocol, Self

from core.domain.architecture.repository import ArchitectureRepository
from core.domain.audit.repository import AuditRepository
from core.domain.capacity.repository import CapacityAnalysisRepository
from core.domain.cost.repository import CostAnalysisRepository, PricingSnapshotRepository
from core.domain.identity.repository import SessionRepository, SingleUseTokenRepository, UserRepository
from core.domain.organizations.repository import (
    InvitationRepository,
    MembershipRepository,
    OrganizationRepository,
)
from core.domain.projects.repository import ProjectRepository
from core.domain.requirements.repository import (
    RequirementAnalysisRepository,
    RequirementRepository,
    RequirementSetRepository,
)
from core.domain.validation.repository import ValidationRunRepository


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

    @property
    def requirement_analyses(self) -> RequirementAnalysisRepository: ...

    @property
    def architectures(self) -> ArchitectureRepository: ...

    @property
    def validations(self) -> ValidationRunRepository: ...

    @property
    def capacity(self) -> CapacityAnalysisRepository: ...

    @property
    def pricing(self) -> PricingSnapshotRepository: ...

    @property
    def cost(self) -> CostAnalysisRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None: ...
