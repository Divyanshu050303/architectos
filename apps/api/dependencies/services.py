"""Wiring: builds domain services for a request. Route handlers only ever receive them."""

from functools import cache
from typing import Annotated

from fastapi import BackgroundTasks, Depends, Request

from apps.api.access_tokens import AccessTokenCodec
from apps.api.config import Settings
from apps.api.email.mailer import BackgroundMailer
from apps.api.email.messages import Links
from apps.api.email.transport import EmailTransport
from apps.api.metrics import LogMetrics
from apps.api.middleware.rate_limit import RateLimiter, RateLimits
from apps.api.middleware.request_id import current_request_id
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.audit.audit_service import AuditService
from core.domain.client import ClientInfo
from core.domain.clock import Clock, utc_now
from core.domain.identity.auth_service import AuthService, VerificationSettings
from core.domain.identity.password_service import PasswordService, ResetSettings
from core.domain.identity.passwords import PasswordHasher, PasswordPolicy
from core.domain.identity.session_service import SessionService, SessionSettings
from core.domain.identity.user_service import UserService
from core.domain.notifications import Mailer
from core.domain.organizations.invitation_service import InvitationService, InvitationSettings
from core.domain.organizations.membership_service import MembershipService
from core.domain.organizations.organization_service import OrganizationService
from core.domain.projects.project_service import ProjectService
from core.domain.requirements.analysis_service import RequirementAnalysisService
from core.domain.requirements.requirement_service import RequirementService
from core.domain.requirements.requirement_set_service import RequirementSetService
from persistence.unit_of_work import SqlAlchemyUnitOfWork

from .database import DbSession


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]


def get_clock() -> Clock:
    return utc_now


def get_client_info(request: Request) -> ClientInfo:
    """Request origin for sessions and audit entries. Behind a proxy, run uvicorn with
    --proxy-headers and --forwarded-allow-ips so request.client is the real client."""
    return ClientInfo(
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
        request_id=current_request_id(),
    )


Client = Annotated[ClientInfo, Depends(get_client_info)]


def get_email_transport(request: Request) -> EmailTransport:
    transport: EmailTransport = request.app.state.email_transport
    return transport


def get_mailer(
    background: BackgroundTasks,
    transport: Annotated[EmailTransport, Depends(get_email_transport)],
    settings: AppSettings,
) -> Mailer:
    return BackgroundMailer(
        transport=transport,
        background=background,
        sender=settings.email_from,
        links=Links(str(settings.frontend_url)),
        verification_ttl=settings.email_verification_ttl,
        password_reset_ttl=settings.password_reset_ttl,
        invitation_ttl=settings.invitation_ttl,
    )


@cache
def _password_hasher(max_concurrency: int) -> PasswordHasher:
    return PasswordHasher(max_concurrency=max_concurrency)


@cache
def _password_policy(min_length: int) -> PasswordPolicy:
    # Cached: the policy holds the ~46k-entry common-password set.
    return PasswordPolicy(min_length=min_length)


def get_auth_service(
    db: DbSession,
    client: Client,
    settings: AppSettings,
    mailer: Annotated[Mailer, Depends(get_mailer)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> AuthService:
    return AuthService(
        SqlAlchemyUnitOfWork(db, client),
        hasher=_password_hasher(settings.password_hash_concurrency),
        policy=_password_policy(settings.password_min_length),
        mailer=mailer,
        verification=VerificationSettings(
            ttl=settings.email_verification_ttl,
            resend_cooldown=settings.email_verification_resend_cooldown,
        ),
        clock=clock,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_session_service(
    db: DbSession, client: Client, settings: AppSettings, clock: Annotated[Clock, Depends(get_clock)]
) -> SessionService:
    return SessionService(
        SqlAlchemyUnitOfWork(db, client),
        hasher=_password_hasher(settings.password_hash_concurrency),
        settings=SessionSettings(
            refresh_ttl=settings.refresh_token_ttl, reuse_grace=settings.refresh_reuse_grace
        ),
        clock=clock,
    )


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]


def get_access_token_codec(settings: AppSettings) -> AccessTokenCodec:
    return AccessTokenCodec(
        secret=settings.access_token_secret.get_secret_value(), ttl=settings.access_token_ttl
    )


def get_password_service(
    db: DbSession,
    client: Client,
    settings: AppSettings,
    mailer: Annotated[Mailer, Depends(get_mailer)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> PasswordService:
    return PasswordService(
        SqlAlchemyUnitOfWork(db, client),
        hasher=_password_hasher(settings.password_hash_concurrency),
        policy=_password_policy(settings.password_min_length),
        mailer=mailer,
        settings=ResetSettings(ttl=settings.password_reset_ttl, cooldown=settings.password_reset_cooldown),
        clock=clock,
    )


PasswordServiceDep = Annotated[PasswordService, Depends(get_password_service)]


def get_user_service(
    db: DbSession, client: Client, settings: AppSettings, clock: Annotated[Clock, Depends(get_clock)]
) -> UserService:
    return UserService(
        SqlAlchemyUnitOfWork(db, client),
        hasher=_password_hasher(settings.password_hash_concurrency),
        avatar_hosts=frozenset(host.lower() for host in settings.avatar_url_allowed_hosts),
        clock=clock,
    )


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


def get_organization_service(
    db: DbSession, client: Client, clock: Annotated[Clock, Depends(get_clock)]
) -> OrganizationService:
    return OrganizationService(SqlAlchemyUnitOfWork(db, client), clock=clock)


OrganizationServiceDep = Annotated[OrganizationService, Depends(get_organization_service)]


def get_membership_service(db: DbSession, client: Client) -> MembershipService:
    return MembershipService(SqlAlchemyUnitOfWork(db, client))


MembershipServiceDep = Annotated[MembershipService, Depends(get_membership_service)]


def get_invitation_service(
    db: DbSession,
    client: Client,
    settings: AppSettings,
    mailer: Annotated[Mailer, Depends(get_mailer)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> InvitationService:
    return InvitationService(
        SqlAlchemyUnitOfWork(db, client),
        mailer=mailer,
        settings=InvitationSettings(ttl=settings.invitation_ttl),
        clock=clock,
    )


InvitationServiceDep = Annotated[InvitationService, Depends(get_invitation_service)]


def get_audit_service(db: DbSession) -> AuditService:
    return AuditService(SqlAlchemyUnitOfWork(db))


AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]


def get_rate_limits(request: Request, settings: AppSettings, client: Client) -> RateLimits:
    limiter: RateLimiter = request.app.state.rate_limiter
    return RateLimits(limiter, client_ip=client.ip_address, enabled=settings.rate_limit_enabled)


RateLimitsDep = Annotated[RateLimits, Depends(get_rate_limits)]


def get_project_service(
    db: DbSession, client: Client, clock: Annotated[Clock, Depends(get_clock)]
) -> ProjectService:
    return ProjectService(SqlAlchemyUnitOfWork(db, client), clock=clock)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


def get_requirement_service(
    db: DbSession, client: Client, clock: Annotated[Clock, Depends(get_clock)]
) -> RequirementService:
    return RequirementService(SqlAlchemyUnitOfWork(db, client), clock=clock)


RequirementServiceDep = Annotated[RequirementService, Depends(get_requirement_service)]


def get_requirement_set_service(
    db: DbSession, client: Client, clock: Annotated[Clock, Depends(get_clock)]
) -> RequirementSetService:
    return RequirementSetService(SqlAlchemyUnitOfWork(db, client), clock=clock)


RequirementSetServiceDep = Annotated[RequirementSetService, Depends(get_requirement_set_service)]


def get_architecture_service(
    db: DbSession, client: Client, clock: Annotated[Clock, Depends(get_clock)]
) -> ArchitectureService:
    return ArchitectureService(SqlAlchemyUnitOfWork(db, client), clock=clock)


ArchitectureServiceDep = Annotated[ArchitectureService, Depends(get_architecture_service)]


def get_requirement_analysis_service(
    request: Request, db: DbSession, client: Client, clock: Annotated[Clock, Depends(get_clock)]
) -> RequirementAnalysisService:
    # One engine per process, built from settings at startup (see apps/api/main.py).
    return RequirementAnalysisService(
        SqlAlchemyUnitOfWork(db, client),
        request.app.state.requirements_engine,
        clock=clock,
        metrics=LogMetrics(),
    )


RequirementAnalysisServiceDep = Annotated[
    RequirementAnalysisService, Depends(get_requirement_analysis_service)
]
