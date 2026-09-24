"""Wiring: builds domain services for a request. Route handlers only ever receive them."""

from functools import cache
from typing import Annotated

from fastapi import Depends, Request

from apps.api.config import Settings
from core.domain.identity.auth_service import AuthService
from core.domain.identity.passwords import PasswordHasher, PasswordPolicy
from persistence.unit_of_work import SqlAlchemyUnitOfWork

from .database import DbSession


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]


@cache
def _password_hasher() -> PasswordHasher:
    return PasswordHasher()


@cache
def _password_policy(min_length: int) -> PasswordPolicy:
    # Cached: the policy holds the ~46k-entry common-password set.
    return PasswordPolicy(min_length=min_length)


def get_auth_service(db: DbSession, settings: AppSettings) -> AuthService:
    return AuthService(
        SqlAlchemyUnitOfWork(db),
        hasher=_password_hasher(),
        policy=_password_policy(settings.password_min_length),
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
