"""Helpers for the security sweeps: route inventory from OpenAPI, principals, error assertions."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient, Response

from apps.api.access_tokens import AccessTokenCodec
from apps.api.email.transport import InMemoryTransport
from tests.integration.api.conftest import TEST_SECRET, token_from

PASSWORD = "correct horse battery staple"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    protected: bool
    has_body: bool
    operation_id: str

    def url(self, **values: str) -> str:
        url = self.path
        for name, value in values.items():
            url = url.replace("{" + name + "}", value)
        return (
            url.replace("{organization_id}", str(uuid.uuid4()))
            .replace("{member_id}", str(uuid.uuid4()))
            .replace("{invitation_id}", str(uuid.uuid4()))
            .replace("{session_id}", str(uuid.uuid4()))
            .replace("{project_id}", str(uuid.uuid4()))
            .replace("{requirement_id}", str(uuid.uuid4()))
            .replace("{version}", "1")
            .replace("{set_id}", str(uuid.uuid4()))
            .replace("{analysis_id}", str(uuid.uuid4()))
            .replace("{architecture_id}", str(uuid.uuid4()))
            .replace("{run_id}", str(uuid.uuid4()))
            .replace("{capacity_analysis_id}", str(uuid.uuid4()))
            .replace("{cost_analysis_id}", str(uuid.uuid4()))
            .replace("{reliability_analysis_id}", str(uuid.uuid4()))
            .replace("{snapshot_id}", str(uuid.uuid4()))
            .replace("{invitation_token}", "A" * 43)
        ) + ("?from=1&to=1" if url.endswith("/compare") else "")


def inventory(app: FastAPI) -> list[Operation]:
    operations = []
    for path, methods in app.openapi()["paths"].items():
        for method, spec in methods.items():
            operations.append(
                Operation(
                    method=method.upper(),
                    path=path,
                    protected=bool(spec.get("security")),
                    has_body="requestBody" in spec,
                    operation_id=spec["operationId"],
                )
            )
    return operations


def assert_error_envelope(response: Response, status: int, code: str | None = None) -> None:
    assert response.status_code == status, (response.status_code, response.text)
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["request_id"] == response.headers["x-request-id"]
    assert isinstance(error["message"], str)
    assert error["message"]
    if code is not None:
        assert error["code"] == code, error


async def signed_in(client: AsyncClient, outbox: InMemoryTransport, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": email[:4]})
    verification = next(m for m in reversed(outbox.outbox) if m["To"] == email)
    await client.post("/api/v1/auth/verify-email", json={"token": token_from(verification)})
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}, headers=WEB
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


def forged_token(*, expired: bool = False, secret: str = TEST_SECRET) -> str:
    now = datetime.now(UTC) - (timedelta(hours=1) if expired else timedelta(0))
    return (
        AccessTokenCodec(secret=secret, ttl=timedelta(minutes=15))
        .issue(user_id=uuid.uuid7(), session_id=uuid.uuid7(), now=now)
        .token
    )


def body_json(response: Response) -> Any:
    return response.json() if response.content else None
