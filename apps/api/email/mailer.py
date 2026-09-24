"""Implements core.domain.notifications.Mailer for HTTP requests.

Messages are handed to FastAPI background tasks, which run after the response has been sent:
the response time never depends on the mail server (and cannot reveal whether an email was
sent), and a delivery failure never fails the request. Failures are logged without the
recipient or the message body, which contains a live token.
"""

import logging
from datetime import timedelta
from email.message import EmailMessage

from fastapi import BackgroundTasks

from apps.api.middleware.request_id import current_request_id

from . import messages
from .transport import EmailTransport

logger = logging.getLogger("architectos.email")


async def deliver(transport: EmailTransport, message: EmailMessage, kind: str) -> None:
    try:
        await transport.send(message)
    except Exception:
        logger.exception(
            "email delivery failed", extra={"email_kind": kind, "request_id": current_request_id()}
        )
    else:
        logger.info("email sent", extra={"email_kind": kind, "request_id": current_request_id()})


class BackgroundMailer:
    def __init__(
        self,
        *,
        transport: EmailTransport,
        background: BackgroundTasks,
        sender: str,
        links: messages.Links,
        verification_ttl: timedelta,
        password_reset_ttl: timedelta,
        invitation_ttl: timedelta,
    ) -> None:
        self._transport = transport
        self._background = background
        self._sender = sender
        self._links = links
        self._verification_hours = max(1, int(verification_ttl.total_seconds() // 3600))
        self._reset_minutes = max(1, int(password_reset_ttl.total_seconds() // 60))
        self._invitation_days = max(1, invitation_ttl.days)

    def _queue(self, message: EmailMessage, kind: str) -> None:
        self._background.add_task(deliver, self._transport, message, kind)

    async def send_email_verification(self, *, to: str, name: str, token: str) -> None:
        message = messages.verification_email(
            sender=self._sender,
            to=to,
            name=name,
            url=self._links.verify_email(token),
            valid_hours=self._verification_hours,
        )
        self._queue(message, "email_verification")

    async def send_account_exists(self, *, to: str, name: str) -> None:
        message = messages.account_exists_email(
            sender=self._sender,
            to=to,
            name=name,
            sign_in_url=self._links.sign_in(),
            reset_url=self._links.forgot_password(),
        )
        self._queue(message, "account_exists")

    async def send_password_reset(self, *, to: str, name: str, token: str) -> None:
        message = messages.password_reset_email(
            sender=self._sender,
            to=to,
            name=name,
            url=self._links.reset_password(token),
            valid_minutes=self._reset_minutes,
        )
        self._queue(message, "password_reset")

    async def send_password_changed(self, *, to: str, name: str) -> None:
        message = messages.password_changed_email(
            sender=self._sender, to=to, name=name, reset_url=self._links.forgot_password()
        )
        self._queue(message, "password_changed")

    async def send_invitation(
        self, *, to: str, inviter_name: str, organization_name: str, role: str, token: str
    ) -> None:
        message = messages.invitation_email(
            sender=self._sender,
            to=to,
            inviter_name=inviter_name,
            organization_name=organization_name,
            role=role,
            url=self._links.accept_invitation(token),
            valid_days=self._invitation_days,
        )
        self._queue(message, "invitation")
