"""Observability (spec section 53): committed domain events become structured log lines with the
request id, actor, organization and project, and never requirement content."""

import logging

import pytest
from httpx import AsyncClient

from .requirement_support import RPS, World, create

pytestmark = pytest.mark.integration

SECRET_STATEMENT = "Primary database password rotates via vault path secret/db-9f2c"


def events(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "architectos.events"]


async def test_committed_events_are_logged_with_their_context(
    client: AsyncClient, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="architectos.events")
    created = await create(client, world, statement=SECRET_STATEMENT)
    response = await client.patch(
        f"{world.base}/{created['id']}",
        json={"expectedVersion": 1, "structuredData": RPS | {"value": 5000}, "changeReason": "Forecast grew"},
        headers=world.ada,
    )
    request_id = response.headers["x-request-id"]

    logged = events(caplog)
    assert [r.event for r in logged] == [  # type: ignore[attr-defined]
        "requirement.created",
        "requirement.version_created",
        "requirement.updated",
    ]
    update = logged[-1]
    context = {
        key: getattr(update, key) for key in ("request_id", "actor_user_id", "organization_id", "project_id")
    }
    assert context == {
        "request_id": request_id,
        "actor_user_id": created["createdByUserId"],
        "organization_id": world.org_id,
        "project_id": world.project_id,
    }
    assert (update.resource_type, update.resource_id) == ("requirement", created["id"])  # type: ignore[attr-defined]
    everything = " ".join(str(r.__dict__) for r in logged)
    assert SECRET_STATEMENT not in everything
    assert "Forecast grew" not in everything


async def test_project_lifecycle_events_carry_the_project(
    client: AsyncClient, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="architectos.events")
    await client.post(f"/api/v1/projects/{world.project_id}/archive", headers=world.ada)
    await client.post(f"/api/v1/projects/{world.project_id}/restore", headers=world.ada)
    logged = events(caplog)
    assert [(r.event, r.project_id) for r in logged] == [  # type: ignore[attr-defined]
        ("project.archived", world.project_id),
        ("project.restored", world.project_id),
    ]


async def test_nothing_is_logged_for_a_rolled_back_change(
    client: AsyncClient, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    created = await create(client, world)
    caplog.set_level(logging.INFO, logger="architectos.events")
    stale = await client.patch(
        f"{world.base}/{created['id']}",
        json={"expectedVersion": 9, "title": "x", "changeReason": "x"},
        headers=world.ada,
    )
    assert stale.status_code == 409
    assert events(caplog) == []
