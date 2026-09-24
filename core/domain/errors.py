"""Base class for business-rule violations.

Each error carries a stable, lower_snake ``code`` that clients may branch on and a message safe
to show to users. The HTTP layer decides the status code; the domain never mentions HTTP.
"""

from typing import Any, ClassVar


class DomainError(Exception):
    code: ClassVar[str] = "domain_error"
    message: ClassVar[str] = "The request could not be completed."

    def __init__(self, message: str | None = None, *, details: Any = None) -> None:
        self.detail_message = message or self.message
        self.details = details
        super().__init__(self.detail_message)
