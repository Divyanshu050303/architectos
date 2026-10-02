"""Drift detection is read-only and bounded (Drift Detection Engine, phase 9): its code cannot execute or
fetch anything, never writes an architecture revision or a discovery result and keeps no drift score;
over HTTP, requests are size- and rate-limited, failures expose no internals and store nothing, and a
person outside a project learns nothing about its drift. Checked on the code and on its behavior."""

import ast
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import DriftAnalysisRecord, DriftItemRecord
from tests.integration.api.requirement_support import make_world, signed_in

ROOT = Path(__file__).resolve().parents[2]
CODE = sorted(
    [
        *(ROOT / "engines" / "drift").glob("*.py"),
        *(ROOT / "core" / "domain" / "drift").glob("*.py"),
        ROOT / "apps" / "api" / "routes" / "drift.py",
        ROOT / "apps" / "api" / "schemas" / "drift.py",
        ROOT / "persistence" / "repositories" / "drift.py",
    ]
)
FORBIDDEN_MODULES = {
    "subprocess", "socket", "urllib", "http", "requests", "httpx", "aiohttp", "pickle", "marshal", "shelve",
    "ctypes", "importlib", "runpy", "shutil", "tempfile", "boto3", "kubernetes", "docker", "os",
}  # fmt: skip
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint"}
# What drift may ask of the architecture and discovery repositories: reads only.
READS = {
    "architectures": {"get", "get_revision"},
    "discoveries": {"get", "accepted_for"},
}
SHOP = "name: shop\nservices:\n  db:\n    image: postgres:16\n"


def _module(name: str) -> bool:
    return name.split(".", 1)[0] in FORBIDDEN_MODULES


@pytest.mark.parametrize("path", CODE, ids=lambda p: str(p.relative_to(ROOT)))
def test_drift_code_cannot_execute_fetch_or_change_an_architecture(path: Path) -> None:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            assert not any(_module(a.name) for a in node.names), (path, node.lineno)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            assert not _module(node.module), (path, node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_CALLS, (path, node.lineno, node.func.id)
        # uow.architectures.<x>(...) / uow.discoveries.<x>(...): only reads.
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
            allowed = READS.get(node.value.attr)
            if allowed is not None:
                assert node.attr in allowed, (path, node.lineno, f"{node.value.attr}.{node.attr}")
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "architecture_service" not in node.module, (path, node.lineno)  # never revises


@pytest.mark.parametrize("path", CODE, ids=lambda p: str(p.relative_to(ROOT)))
def test_there_is_no_drift_score(path: Path) -> None:
    names = {
        n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else n.arg
        for n in ast.walk(ast.parse(path.read_text()))
        if isinstance(n, ast.Name | ast.Attribute | ast.arg)
    }
    assert not {n for n in names if "score" in n.lower()}, path


# --- over HTTP ------------------------------------------------------------------------------------


async def baseline(client: AsyncClient, auth: dict[str, str], project_id: str) -> tuple[str, str]:
    runs = f"/api/v1/projects/{project_id}/discovery-runs"
    run = await client.post(runs, json={"artifacts": [{"path": "c.yaml", "content": SHOP}]}, headers=auth)
    run_id = run.json()["id"]
    proposal = (await client.get(f"{runs}/{run_id}/proposal", headers=auth)).json()
    accepted = await client.post(
        f"{runs}/{run_id}/accept",
        json={"proposalContentHash": proposal["contentHash"], "name": "S"},
        headers=auth,
    )
    assert accepted.status_code == 201, accepted.text
    return str(accepted.json()["architectureId"]), str(run_id)


async def test_requests_are_size_and_rate_limited(client: AsyncClient, outbox: InMemoryTransport) -> None:
    world = await make_world(client, outbox)
    url = f"/api/v1/projects/{world.project_id}/drift-analyses"
    body = {"architectureId": str(uuid.uuid4()), "baselineRevision": 1, "discoveryRunId": str(uuid.uuid4())}
    huge = body | {"label": "x" * (70 * 1024)}  # beyond the 64 KiB a drift request needs
    too_big = await client.post(url, json=huge, headers=world.ada)
    assert (too_big.status_code, too_big.json()["error"]["code"]) == (413, "payload_too_large")
    too_many = await client.post(url, json=body | {"exclude": ["db"] * 501}, headers=world.ada)
    assert too_many.status_code == 422
    for attempt in range(60):  # refused requests count: the limit is on attempts
        response = await client.post(url, json=body, headers=world.ada)
        assert response.status_code == 404, (attempt, response.text)
    limited = await client.post(url, json=body, headers=world.ada)
    assert (limited.status_code, limited.json()["error"]["code"]) == (429, "rate_limited")
    assert int(limited.headers["retry-after"]) > 0


async def test_failures_expose_nothing_and_store_nothing(
    app: FastAPI, client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport
) -> None:
    world = await make_world(client, outbox)
    aid, run_id = await baseline(client, world.ada, world.project_id)

    class Broken:
        def run(self, inputs: Any) -> Any:
            raise RuntimeError("internal detail sk_live_do_not_leak at /srv/engine.py")

        def versions(self) -> dict[str, int]:
            return {}

    app.state.drift_engine = Broken()
    url = f"/api/v1/projects/{world.project_id}/drift-analyses"
    body = {"architectureId": aid, "baselineRevision": 1, "discoveryRunId": run_id}
    response = await client.post(url, json=body, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (500, "internal_error")
    assert "sk_live" not in response.text
    assert "/srv/" not in response.text
    assert await db.scalar(select(func.count()).select_from(DriftAnalysisRecord)) == 0
    assert await db.scalar(select(func.count()).select_from(DriftItemRecord)) == 0

    malformed = await client.post(url, content=b"{not json", headers=world.ada | {"content-type": "x"})
    assert malformed.status_code in {415, 422}
    assert "Traceback" not in malformed.text
    bad_id = await client.get(f"{url}/not-a-uuid", headers=world.ada)
    assert (bad_id.status_code, bad_id.json()["error"]["code"]) == (422, "validation_error")


async def test_another_organization_learns_nothing(client: AsyncClient, outbox: InMemoryTransport) -> None:
    world = await make_world(client, outbox)
    aid, run_id = await baseline(client, world.ada, world.project_id)
    analyses = f"/api/v1/projects/{world.project_id}/drift-analyses"
    body = {"architectureId": aid, "baselineRevision": 1, "discoveryRunId": run_id}
    analysis = (await client.post(analyses, json=body, headers=world.ada)).json()

    eve = await signed_in(client, outbox, "eve@example.com")
    org = (await client.post("/api/v1/organizations", json={"name": "Globex"}, headers=eve)).json()["id"]
    own = (await client.post(f"/api/v1/organizations/{org}/projects", json={"name": "G"}, headers=eve)).json()
    probes: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", f"{analyses}/{analysis['id']}", None),
        ("POST", analyses, body),
        # Their own project, someone else's ids: neither the architecture nor the run is theirs.
        ("POST", f"/api/v1/projects/{own['id']}/drift-analyses", body),
        ("GET", f"/api/v1/projects/{own['id']}/drift-analyses/{analysis['id']}", None),
        ("GET", f"/api/v1/projects/{own['id']}/architectures/{aid}/identity-mappings", None),
    ]
    for method, path, payload in probes:
        response = await client.request(method, path, json=payload, headers=eve)
        assert response.status_code == 404, (path, response.text)
        assert response.json()["error"]["code"] in {
            "project_not_found", "architecture_not_found", "drift_analysis_not_found",
        }  # fmt: skip
        assert analysis["id"] not in json.dumps(response.json()["error"].get("details") or {})
