"""Wiring: builds domain services for a request. Route handlers only ever receive them."""

from functools import cache
from typing import Annotated

from fastapi import BackgroundTasks, Depends, Request

from apps.api.config import Settings
from apps.api.email.mailer import BackgroundMailer
from apps.api.email.messages import Links
from apps.api.email.transport import EmailTransport
from core.domain.clock import Clock, utc_now
from core.domain.identity.auth_service import AuthService, VerificationSettings
from core.domain.identity.passwords import PasswordHasher, PasswordPolicy
from core.domain.notifications import Mailer
from persistence.unit_of_work import SqlAlchemyUnitOfWork

from .database import DbSession


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]


def get_clock() -> Clock:
    return utc_now


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
    )


@cache
def _password_hasher() -> PasswordHasher:
    return PasswordHasher()


@cache
def _password_policy(min_length: int) -> PasswordPolicy:
    # Cached: the policy holds the ~46k-entry common-password set.
    return PasswordPolicy(min_length=min_length)


def get_auth_service(
    db: DbSession,
    settings: AppSettings,
    mailer: Annotated[Mailer, Depends(get_mailer)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> AuthService:
    return AuthService(
        SqlAlchemyUnitOfWork(db),
        hasher=_password_hasher(),
        policy=_password_policy(settings.password_min_length),
        mailer=mailer,
        verification=VerificationSettings(
            ttl=settings.email_verification_ttl,
            resend_cooldown=settings.email_verification_resend_cooldown,
        ),
        clock=clock,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
