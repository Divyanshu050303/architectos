from typing import Annotated

from pydantic import Field, SecretStr

from .common import RequestModel

# Input caps bound the work done before validation (Argon2 on megabyte-long strings is a DoS).
# The real rules live in the domain (core/domain/identity); these only reject absurd input.
EmailInput = Annotated[str, Field(max_length=512, examples=["ada@example.com"])]
PasswordInput = Annotated[SecretStr, Field(max_length=1024)]


class RegisterRequest(RequestModel):
    email: EmailInput
    password: PasswordInput
    name: Annotated[str, Field(max_length=200, examples=["Ada Lovelace"])]


class VerifyEmailRequest(RequestModel):
    token: Annotated[str, Field(min_length=1, max_length=256, description="The token from the emailed link.")]


class ResendVerificationRequest(RequestModel):
    email: EmailInput
