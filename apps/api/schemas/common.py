"""Shared API model conventions.

JSON uses camelCase (matching apps/web); Python uses snake_case. Request models reject unknown
fields, so a typo or an attempt to set a field the endpoint does not accept is an error.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True)


class RequestModel(ApiModel):
    model_config = ConfigDict(extra="forbid")


class ErrorBody(BaseModel):
    code: str = Field(description="Stable lower_snake identifier, e.g. weak_password.")
    message: str = Field(description="Human-readable; safe to show to users.")
    details: Any | None = Field(default=None, description="Code-specific extra data.")
    request_id: str | None = Field(default=None, description="Matches the X-Request-ID response header.")


class ErrorResponse(BaseModel):
    """Every non-2xx response has this shape."""

    error: ErrorBody


class MessageResponse(ApiModel):
    message: str
