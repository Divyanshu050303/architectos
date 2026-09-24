"""The refresh-token cookie.

HttpOnly (no JavaScript access), Secure, SameSite (Lax by default) and scoped to the auth
routes, so it is sent only to login/refresh/logout and never to ordinary API calls. With Secure
on, the name carries the __Secure- prefix, which browsers only accept from secure contexts.
"""

from datetime import datetime

from starlette.responses import Response

from apps.api.config import Settings

COOKIE_PATH = "/api/v1/auth"


def refresh_cookie_name(settings: Settings) -> str:
    return "__Secure-architectos_refresh" if settings.cookie_secure else "architectos_refresh"


def set_refresh_cookie(
    response: Response, settings: Settings, *, token: str, expires_at: datetime, now: datetime
) -> None:
    response.set_cookie(
        refresh_cookie_name(settings),
        token,
        max_age=max(0, int((expires_at - now).total_seconds())),
        path=COOKIE_PATH,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        refresh_cookie_name(settings),
        path=COOKIE_PATH,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
