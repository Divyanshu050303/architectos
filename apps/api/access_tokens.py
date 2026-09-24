"""Access tokens: short-lived JWTs proving "user X, signed in through session Y".

HS256 with a server-side secret; the algorithm is pinned on decode (no "none", no algorithm
confusion). Issuer, audience and a "typ" claim are required, so a token minted for another
purpose or service is rejected. Expiry is checked against the injected clock. A valid token is
necessary but not sufficient: the dependency also checks the session is still active.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

from core.domain.errors import DomainError

ALGORITHM = "HS256"
ISSUER = "architectos-api"
AUDIENCE = "architectos-web"
TOKEN_TYPE = "access"  # noqa: S105 — the "typ" claim value, not a secret


class Unauthenticated(DomainError):
    code = "unauthenticated"
    message = "Sign in to continue."


class InvalidAccessToken(DomainError):
    code = "invalid_access_token"
    message = "The access token is invalid."


class AccessTokenExpired(DomainError):
    """The client should call POST /auth/refresh and retry."""

    code = "access_token_expired"
    message = "The access token has expired."


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    token: str
    expires_in: int


class AccessTokenCodec:
    def __init__(self, *, secret: str, ttl: timedelta) -> None:
        self._secret = secret
        self._ttl = ttl

    def issue(self, *, user_id: uuid.UUID, session_id: uuid.UUID, now: datetime) -> IssuedAccessToken:
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "typ": TOKEN_TYPE,
            "sub": str(user_id),
            "sid": str(session_id),
            "iat": int(now.timestamp()),
            "exp": int((now + self._ttl).timestamp()),
        }
        return IssuedAccessToken(
            token=jwt.encode(claims, self._secret, algorithm=ALGORITHM),
            expires_in=int(self._ttl.total_seconds()),
        )

    def decode(self, token: str, *, now: datetime) -> AccessClaims:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                audience=AUDIENCE,
                issuer=ISSUER,
                # Time claims are checked below against the service clock, not time.time().
                options={
                    "require": ["iss", "aud", "sub", "sid", "typ", "iat", "exp"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
            if claims["typ"] != TOKEN_TYPE:
                raise InvalidAccessToken
            parsed = AccessClaims(
                user_id=uuid.UUID(claims["sub"]),
                session_id=uuid.UUID(claims["sid"]),
                expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=now.tzinfo),
            )
        except jwt.InvalidTokenError, ValueError, TypeError, KeyError:
            raise InvalidAccessToken from None
        if now >= parsed.expires_at:
            raise AccessTokenExpired
        return parsed
