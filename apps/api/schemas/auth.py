from typing import Annotated, Literal

from pydantic import Field

from .common import ApiModel, RequestModel
from .fields import EmailInput, PasswordInput
from .users import UserResponse


class RegisterRequest(RequestModel):
    email: EmailInput
    password: PasswordInput
    name: Annotated[str, Field(max_length=200, examples=["Ada Lovelace"])]


class VerifyEmailRequest(RequestModel):
    token: Annotated[str, Field(min_length=1, max_length=256, description="The token from the emailed link.")]


class ResendVerificationRequest(RequestModel):
    email: EmailInput


class LoginRequest(RequestModel):
    email: EmailInput
    password: PasswordInput


class SessionResponse(ApiModel):
    """Returned by login and refresh. The refresh token is only ever in the HttpOnly cookie."""

    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 — OAuth token type name, not a secret
    expires_in: int = Field(description="Access token lifetime in seconds.")
    user: UserResponse


class ForgotPasswordRequest(RequestModel):
    email: EmailInput


class ResetPasswordRequest(RequestModel):
    token: Annotated[str, Field(min_length=1, max_length=256, description="The token from the emailed link.")]
    password: PasswordInput


class ChangePasswordRequest(RequestModel):
    current_password: PasswordInput
    new_password: PasswordInput
