"""SmtpTransport against the Mailpit server from docker-compose (make db-up)."""

import json
import uuid
from urllib.request import Request, urlopen

import pytest

from apps.api.email.messages import Links, verification_email
from apps.api.email.transport import SmtpTransport

pytestmark = pytest.mark.integration

MAILPIT_API = "http://127.0.0.1:8025/api/v1"


def mailpit(method: str, path: str, body: dict[str, object] | None = None) -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(  # noqa: S310 — fixed local URL
        f"{MAILPIT_API}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urlopen(request, timeout=5) as response:  # noqa: S310
        raw: bytes = response.read()
    return raw


async def test_verification_email_is_delivered_over_smtp() -> None:
    recipient = f"smtp-{uuid.uuid4().hex[:8]}@example.com"
    message = verification_email(
        sender="ArchitectOS <no-reply@localhost>",
        to=recipient,
        name="Ada",
        url=Links("http://localhost:3000").verify_email("tok123"),
        valid_hours=24,
    )
    transport = SmtpTransport(
        host="127.0.0.1", port=1025, security="none", username=None, password=None, timeout_seconds=5
    )

    await transport.send(message)

    messages = json.loads(mailpit("GET", f"/search?query=to:{recipient}"))["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 1
    assert messages[0]["Subject"] == "Verify your email address"
    # Remove only the message this test sent.
    mailpit("DELETE", "/messages", {"IDs": [messages[0]["ID"]]})
