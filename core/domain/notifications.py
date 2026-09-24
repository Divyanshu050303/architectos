"""Outgoing email, as the domain sees it. Providers, templates and links live in the API layer.

Services call these only after their transaction commits, so no email is sent for work that
was rolled back. Raw tokens are handed to the mailer and nowhere else.
"""

from typing import Protocol


class Mailer(Protocol):
    async def send_email_verification(self, *, to: str, name: str, token: str) -> None: ...

    async def send_account_exists(self, *, to: str, name: str) -> None:
        """Someone tried to register an email that already has a verified account."""
        ...
