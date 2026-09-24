from fastapi import APIRouter, status

from apps.api.dependencies.services import AuthServiceDep
from apps.api.schemas.auth import RegisterRequest, ResendVerificationRequest, VerifyEmailRequest
from apps.api.schemas.common import ErrorResponse, MessageResponse

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
