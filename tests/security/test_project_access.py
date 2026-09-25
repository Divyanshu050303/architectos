"""Project authorization beyond the sweeps: access follows membership changes immediately, and
pagination and search stay inside the caller's organization. Acme: Ada (owner), Mel (member)."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import OrganizationRecord, ProjectRecord
from tests.integration.api.conftest import token_from

from .support import assert_error_envelope, signed_in


@pytest.fixture
async def acme(client: AsyncClient, outbox: InMemoryTransport) -> dict[str, str]:
    """Acme with an owner (Ada), a member (Mel) and one project. Returns ids and headers."""
    ada = await signed_in(client, outbox, "ada@example.com")
    org = (await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=ada)).json()["id"]
    await client.post(
        f"/api/v1/organizations/{org}/invitations",
        json={"email": "mel@example.com", "role": "member"},
        headers=ada,
    )
    invite = next(m for m in reversed(outbox.outbox) if "Join" in str(m["Subject"]))
    invite_token = token_from(invite)
    mel = await signed_in(client, outbox, "mel@example.com")
    assert (await client.post(f"/api/v1/invitations/{invite_token}/accept", headers=mel)).status_code == 200
    project = (
        await client.post(
            f"/api/v1/organizations/{org}/projects", json={"name": "Food Delivery"}, headers=ada
        )
    ).json()
    members = (await client.get(f"/api/v1/organizations/{org}/members", headers=ada)).json()["members"]
    mel_member = next(m["id"] for m in members if m["email"] == "mel@example.com")
    return {
        "org": org,
        "project": project["id"],
        "mel_member": mel_member,
        "ada": ada["Authorization"],
        "mel": mel["Authorization"],
    }


def auth(token: str) -> dict[str, str]:
    return {"Authorization": token}


async def can_read(client: AsyncClient, ctx: dict[str, str], who: str) -> int:
    return (await client.get(f"/api/v1/projects/{ctx['project']}", headers=auth(ctx[who]))).status_code


async def test_leaving_the_organization_removes_project_access_at_once(
    client: AsyncClient, acme: dict[str, str]
) -> None:
    assert await can_read(client, acme, "mel") == 200
    left = await client.delete(
        f"/api/v1/organizations/{acme['org']}/members/{acme['mel_member']}", headers=auth(acme["mel"])
    )
    assert left.status_code == 204

    response = await client.get(f"/api/v1/projects/{acme['project']}", headers=auth(acme["mel"]))
    assert_error_envelope(response, 404, "project_not_found")
    listing = await client.get(f"/api/v1/organizations/{acme['org']}/projects", headers=auth(acme["mel"]))
    assert_error_envelope(listing, 404, "organization_not_found")


async def test_being_removed_by_an_admin_removes_project_access(
    client: AsyncClient, acme: dict[str, str]
) -> None:
    removed = await client.delete(
        f"/api/v1/organizations/{acme['org']}/members/{acme['mel_member']}", headers=auth(acme["ada"])
    )
    assert removed.status_code == 204
    assert await can_read(client, acme, "mel") == 404
    patch = await client.patch(
        f"/api/v1/projects/{acme['project']}", json={"name": "x"}, headers=auth(acme["mel"])
    )
    assert_error_envelope(patch, 404, "project_not_found")


async def test_a_demotion_applies_to_the_very_next_request(client: AsyncClient, acme: dict[str, str]) -> None:
    url = f"/api/v1/projects/{acme['project']}"
    assert (await client.patch(url, json={"name": "By Mel"}, headers=auth(acme["mel"]))).status_code == 200
    demoted = await client.patch(
        f"/api/v1/organizations/{acme['org']}/members/{acme['mel_member']}",
        json={"role": "viewer"},
        headers=auth(acme["ada"]),
    )
    assert demoted.status_code == 200

    response = await client.patch(url, json={"name": "Again"}, headers=auth(acme["mel"]))
    assert_error_envelope(response, 403, "permission_denied")
    assert await can_read(client, acme, "mel") == 200  # viewers still read


async def test_deleting_the_organization_hides_its_projects(
    client: AsyncClient, db: AsyncSession, acme: dict[str, str]
) -> None:
    assert (
        await client.delete(f"/api/v1/organizations/{acme['org']}", headers=auth(acme["ada"]))
    ).status_code == 204
    for who in ("ada", "mel"):
        assert await can_read(client, acme, who) == 404
    # Soft delete: the organization and its project are retained; only access is gone.
    organization = await db.get(OrganizationRecord, uuid.UUID(acme["org"]), populate_existing=True)
    project = await db.get(ProjectRecord, uuid.UUID(acme["project"]), populate_existing=True)
    assert organization is not None
    assert organization.deleted_at is not None
    assert project is not None
    assert project.deleted_at is None


async def test_a_project_cannot_be_moved_or_planted_across_organizations(
    client: AsyncClient, outbox: InMemoryTransport, acme: dict[str, str]
) -> None:
    grace = await signed_in(client, outbox, "grace@example.com")
    globex = (await client.post("/api/v1/organizations", json={"name": "Globex"}, headers=grace)).json()["id"]

    moved = await client.patch(
        f"/api/v1/projects/{acme['project']}", json={"organizationId": globex}, headers=auth(acme["ada"])
    )
    assert_error_envelope(moved, 422, "validation_error")
    planted = await client.post(
        f"/api/v1/organizations/{globex}/projects", json={"name": "Planted"}, headers=auth(acme["ada"])
    )
    assert_error_envelope(planted, 404, "organization_not_found")
    project = (await client.get(f"/api/v1/projects/{acme['project']}", headers=auth(acme["ada"]))).json()
    assert project["organizationId"] == acme["org"]
    globex_projects = (await client.get(f"/api/v1/organizations/{globex}/projects", headers=grace)).json()[
        "projects"
    ]
    assert globex_projects == []


async def test_pagination_and_search_never_cross_organizations(
    client: AsyncClient, outbox: InMemoryTransport, acme: dict[str, str]
) -> None:
    ada = auth(acme["ada"])
    for name in ("Alpha", "Bravo", "Secret Plans"):
        await client.post(f"/api/v1/organizations/{acme['org']}/projects", json={"name": name}, headers=ada)
    acme_cursor = (
        await client.get(f"/api/v1/organizations/{acme['org']}/projects", params={"limit": 1}, headers=ada)
    ).json()["nextCursor"]
    assert acme_cursor

    grace = await signed_in(client, outbox, "grace@example.com")
    globex = (await client.post("/api/v1/organizations", json={"name": "Globex"}, headers=grace)).json()["id"]
    await client.post(
        f"/api/v1/organizations/{globex}/projects", json={"name": "Globex Project"}, headers=grace
    )

    # Acme's cursor used on Globex's list only positions the page: it never yields Acme's rows.
    with_cursor = await client.get(
        f"/api/v1/organizations/{globex}/projects", params={"cursor": acme_cursor}, headers=grace
    )
    assert with_cursor.status_code == 200
    assert all(p["organizationId"] == globex for p in with_cursor.json()["projects"])
    # Search in Globex for an Acme project name finds nothing.
    searched = await client.get(
        f"/api/v1/organizations/{globex}/projects", params={"search": "secret"}, headers=grace
    )
    assert searched.json()["projects"] == []
    # And Grace cannot list Acme at all, with or without a cursor or a search.
    for params in ({}, {"cursor": acme_cursor}, {"search": "secret"}):
        denied = await client.get(
            f"/api/v1/organizations/{acme['org']}/projects", params=params, headers=grace
        )
        assert_error_envelope(denied, 404, "organization_not_found")


async def test_signed_out_callers_are_refused_before_anything_else(
    client: AsyncClient, acme: dict[str, str]
) -> None:
    for method, url in (
        ("GET", f"/api/v1/organizations/{acme['org']}/projects"),
        ("GET", f"/api/v1/projects/{acme['project']}"),
        ("PATCH", f"/api/v1/projects/{acme['project']}"),
    ):
        response = await client.request(method, url, json={"name": "x"} if method == "PATCH" else None)
        assert_error_envelope(response, 401, "unauthenticated")
