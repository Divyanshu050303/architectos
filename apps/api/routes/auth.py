from fastapi import APIRouter, status

from apps.api.dependencies.services import AuthServiceDep
from apps.api.schemas.auth import RegisterRequest
from apps.api.schemas.common import ErrorResponse, MessageResponse

router = APIRouter(prefix="/auth", tags=["authentication"])

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
