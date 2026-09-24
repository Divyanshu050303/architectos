"""Request authentication.

- ``CurrentUser``: requires a valid Bearer access token whose session is still active.
- ``require_same_origin``: guards the cookie-authenticated endpoints (login, refresh, logout)
  against cross-site request forgery. Browsers cannot attach the custom header cross-origin
  without a CORS preflight, which the allow-list rejects; the Origin check is a second layer.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from apps.api.access_tokens import AccessTokenCodec, Unauthenticated
from core.domain.clock import Clock
from core.domain.errors import DomainError
from core.domain.identity.entities import Session, User

from .services import AppSettings, SessionServiceDep, get_access_token_codec, get_clock

CSRF_HEADER = "X-Requested-With"
CSRF_HEADER_VALUE = "architectos"

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /auth/login or /auth/refresh")


class CsrfRejected(DomainError):
    code = "csrf_rejected"
    message = "This request must come from the ArchitectOS web app."


@dataclass(frozen=True, slots=True)
class Authenticated:
    user: User
    session: Session


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    codec: Annotated[AccessTokenCodec, Depends(get_access_token_codec)],
    sessions: SessionServiceDep,
    clock: Annotated[Clock, Depends(get_clock)],
) -> Authenticated:
    if credentials is None:
        raise Unauthenticated
    claims = codec.decode(credentials.credentials, now=clock())
    user, session = await sessions.authenticate(session_id=claims.session_id, user_id=claims.user_id)
    return Authenticated(user=user, session=session)


CurrentUser = Annotated[Authenticated, Depends(get_current_user)]


def require_same_origin(request: Request, settings: AppSettings) -> None:
    if request.headers.get(CSRF_HEADER) != CSRF_HEADER_VALUE:
        raise CsrfRejected
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.cors_allowed_origins:
        raise CsrfRejected
