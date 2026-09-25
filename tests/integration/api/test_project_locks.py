"""Which lock each write takes on its project row (see ProjectLock). Requirement edits and
deletes share it, so they run in parallel; everything that changes the project, allocates a
number or snapshots the project takes it exclusively, so it waits for (and blocks) them."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection

from .requirement_support import World, create

pytestmark = pytest.mark.integration

PROJECT_LOCK = re.compile(r"FOR (SHARE|UPDATE) OF projects")


@contextmanager
def project_locks(connection: AsyncConnection) -> Iterator[list[str]]:
    locks: list[str] = []

    def record(*args: Any) -> None:
        locks.extend(PROJECT_LOCK.findall(str(args[2])))

    sync = connection.sync_connection
    assert sync is not None
    event.listen(sync, "before_cursor_execute", record)
    try:
        yield locks
    finally:
        event.remove(sync, "before_cursor_execute", record)


async def test_lock_modes(client: AsyncClient, connection: AsyncConnection, world: World) -> None:
    requirement = await create(client, world, status="draft")
    url = f"{world.base}/{requirement['id']}"
    project = f"/api/v1/projects/{world.project_id}"
    calls = [
        (
            "create requirement",
            "POST",
            world.base,
            {
                "type": "functional",
                "category": "order",
                "title": "t",
                "statement": "s",
                "priority": "low",
                "status": "active",
            },
            ["UPDATE"],
        ),
        ("edit requirement", "PATCH", url, {"expectedVersion": 1, "title": "Renamed"}, ["SHARE"]),
        ("create set", "POST", f"{project}/requirement-sets", {}, ["UPDATE"]),
        ("delete requirement", "DELETE", url, None, ["SHARE"]),
        ("read requirements", "GET", world.base, None, []),
        ("update project", "PATCH", project, {"name": "Renamed"}, ["UPDATE"]),
        ("archive project", "POST", f"{project}/archive", None, ["UPDATE"]),
    ]
    for label, method, target, body, expected in calls:
        with project_locks(connection) as locks:
            response = await client.request(method, target, json=body, headers=world.ada)
        assert response.is_success, (label, response.text)
        assert locks == expected, label
