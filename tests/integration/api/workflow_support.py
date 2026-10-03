"""Architecture workflows in API tests: a real worker (the app's engines, its scripted agent pipeline, the
real controller, store and adapters) on the test's connection, run until the queue is empty."""

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.conftest import joined_session
from workers.workflow_worker import build_worker

MAX_CLAIMS = 20


async def drain(app: FastAPI, connection: AsyncConnection, owner: str = "test-worker") -> int:
    """Claims and advances workflows until none is left to claim; how many claims it took."""
    worker = build_worker(app.state, app.state.settings, lambda: joined_session(connection), owner)
    for claims in range(MAX_CLAIMS):
        if await worker.run_once() is None:
            return claims
    raise AssertionError("the queue never emptied")


def workflows(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/architecture-workflows"


async def start_workflow(
    client: AsyncClient, auth: dict[str, str], project_id: str, **body: Any
) -> dict[str, Any]:
    request = {"objective": "An order service for a web shop"} | body
    response = await client.post(workflows(project_id), json=request, headers=auth)
    assert response.status_code == 202, response.text
    started: dict[str, Any] = response.json()
    return started


async def read_workflow(
    client: AsyncClient, auth: dict[str, str], project_id: str, workflow_id: str
) -> dict[str, Any]:
    response = await client.get(f"{workflows(project_id)}/{workflow_id}", headers=auth)
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def approvable(workflow: dict[str, Any]) -> dict[str, Any]:
    """The approval body for the first candidate of the review package that can be approved."""
    candidate = next(c for c in workflow["candidates"] if c["approvability"]["approvable"])
    return {"candidateId": candidate["id"], "candidateContentHash": candidate["contentHash"]}
