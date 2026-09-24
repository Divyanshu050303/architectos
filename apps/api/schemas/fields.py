"""Field types shared by request schemas.

Input caps bound the work done before validation (Argon2 on megabyte-long strings is a DoS).
The real rules live in the domain (core/domain/identity); these only reject absurd input.
"""

from typing import Annotated

from pydantic import Field, SecretStr

EmailInput = Annotated[str, Field(max_length=512, examples=["ada@example.com"])]
PasswordInput = Annotated[SecretStr, Field(max_length=1024)]
