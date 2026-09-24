"""How a finished message leaves the process. SMTP covers Mailpit locally and any relay or
provider in production (SES, Postmark, SendGrid all accept SMTP)."""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from typing import Literal, Protocol

SmtpSecurity = Literal["none", "starttls", "tls"]


class EmailTransport(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class SmtpTransport:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        security: SmtpSecurity,
        username: str | None,
        password: str | None,
        timeout_seconds: float,
    ) -> None:
        self._host = host
        self._port = port
        self._security = security
        self._username = username
        self._password = password
        self._timeout = timeout_seconds

    async def send(self, message: EmailMessage) -> None:
        # smtplib is blocking; run it off the event loop.
        await asyncio.to_thread(self._send_blocking, message)

    def _send_blocking(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        client: smtplib.SMTP
        if self._security == "tls":
            client = smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout, context=context)
        else:
            client = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        with client:
            if self._security == "starttls":
                client.starttls(context=context)
            if self._username and self._password:
                client.login(self._username, self._password)
            client.send_message(message)


class InMemoryTransport:
    """Collects messages instead of sending them. For tests."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)
