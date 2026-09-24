from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from apps.api.access_tokens import AccessTokenCodec
from apps.api.config import Settings
from apps.api.cookies import clear_refresh_cookie, refresh_cookie_name, set_refresh_cookie
from apps.api.dependencies.auth import require_same_origin
from apps.api.dependencies.services import (
    AppSettings,
    AuthServiceDep,
    SessionServiceDep,
    get_access_token_codec,
    get_clock,
)
from apps.api.exception_handlers import domain_error_response
from apps.api.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    ResendVerificationRequest,
    SessionResponse,
    VerifyEmailRequest,
)
from apps.api.schemas.common import ErrorResponse, MessageResponse
from apps.api.schemas.users import UserResponse
from core.domain.clock import Clock
from core.domain.identity.errors import InvalidRefreshToken, SessionExpired
from core.domain.identity.session_service import ClientInfo, SignedIn

router = APIRouter(prefix="/auth", tags=["authentication"])

VERIFICATION_SENT = "If this address has an unverified account, we sent it a new verification link."
EMAIL_VERIFIED = "Your email address is verified."

REGISTRATION_ACCEPTED = (
    "Check your inbox for a link to verify your email address. "
    "If you already have an account, sign in instead."
)


@router.post(
    "/register",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MessageResponse,
    responses={422: {"model": ErrorResponse, "description": "invalid_email, invalid_name, weak_password"}},
    summary="Create an account",
    description=(
        "Always answers 202 with the same body for a valid request, whether or not the email is "
        "already registered, so the endpoint cannot be used to discover accounts."
    ),
)
async def register(body: RegisterRequest, auth: AuthServiceDep) -> MessageResponse:
    await auth.register(email=body.email, password=body.password.get_secret_value(), name=body.name)
    return MessageResponse(message=REGISTRATION_ACCEPTED)


@router.post(
    "/verify-email",
    response_model=MessageResponse,
    responses={400: {"model": ErrorResponse, "description": "invalid_token, token_expired"}},
    summary="Confirm an email address with the emailed token",
    description="Tokens are single-use. Verifying also invalidates any other outstanding verification link.",
)
async def verify_email(body: VerifyEmailRequest, auth: AuthServiceDep) -> MessageResponse:
    await auth.verify_email(token=body.token)
    return MessageResponse(message=EMAIL_VERIFIED)


@router.post(
    "/resend-verification",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MessageResponse,
    summary="Send a new verification link",
    description=(
        "Always 202 with the same body. A link is sent only to an existing, unverified account, at most "
        "once per cooldown period; the previous link stops working."
    ),
)
async def resend_verification(body: ResendVerificationRequest, auth: AuthServiceDep) -> MessageResponse:
    await auth.resend_verification(email=body.email)
    return MessageResponse(message=VERIFICATION_SENT)


# --- sessions -----------------------------------------------------------------------------------

Codec = Annotated[AccessTokenCodec, Depends(get_access_token_codec)]
ClockDep = Annotated[Clock, Depends(get_clock)]
SAME_ORIGIN = [Depends(require_same_origin)]


def _session_response(
    signed_in: SignedIn, response: Response, *, codec: AccessTokenCodec, settings: Settings, clock: Clock
) -> SessionResponse:
    now = clock()
    access = codec.issue(user_id=signed_in.user.id, session_id=signed_in.session.id, now=now)
    set_refresh_cookie(
        response, settings, token=signed_in.refresh_token, expires_at=signed_in.session.expires_at, now=now
    )
    # Token responses must never be cached (RFC 6749 section 5.1).
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        access_token=access.token, expires_in=access.expires_in, user=UserResponse.from_user(signed_in.user)
    )


def _client_info(request: Request) -> ClientInfo:
    return ClientInfo(
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )


@router.post(
    "/login",
    response_model=SessionResponse,
    dependencies=SAME_ORIGIN,
    responses={
        401: {
            "model": ErrorResponse,
            "description": "invalid_credentials (wrong password and unknown email alike)",
        },
        403: {"model": ErrorResponse, "description": "account_disabled, csrf_rejected"},
    },
    summary="Sign in with email and password",
    description=(
        "Returns a short-lived access token and sets the refresh token as an HttpOnly cookie. "
        "Requires the header X-Requested-With: architectos."
    ),
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    sessions: SessionServiceDep,
    codec: Codec,
    settings: AppSettings,
    clock: ClockDep,
) -> SessionResponse:
    signed_in = await sessions.login(
        email=body.email, password=body.password.get_secret_value(), client=_client_info(request)
    )
    return _session_response(signed_in, response, codec=codec, settings=settings, clock=clock)


@router.post(
    "/refresh",
    response_model=SessionResponse,
    dependencies=SAME_ORIGIN,
    responses={
        401: {
            "model": ErrorResponse,
            "description": "invalid_refresh_token, session_expired (cookie is cleared)",
        },
        409: {
            "model": ErrorResponse,
            "description": "refresh_conflict: rotated by a concurrent request; retry",
        },
    },
    summary="Exchange the refresh cookie for a new access token",
    description=(
        "Rotates the refresh token. Presenting a rotated-out token after the grace window "
        "revokes the session."
    ),
)
async def refresh(
    request: Request,
    response: Response,
    sessions: SessionServiceDep,
    codec: Codec,
    settings: AppSettings,
    clock: ClockDep,
) -> SessionResponse | JSONResponse:
    token = request.cookies.get(refresh_cookie_name(settings))
    try:
        if not token:
            raise InvalidRefreshToken
        signed_in = await sessions.refresh(refresh_token=token)
    except (InvalidRefreshToken, SessionExpired) as error:
        # The cookie is dead: tell the browser to drop it along with the error.
        failure = domain_error_response(error)
        clear_refresh_cookie(failure, settings)
        return failure
    return _session_response(signed_in, response, codec=codec, settings=settings, clock=clock)
