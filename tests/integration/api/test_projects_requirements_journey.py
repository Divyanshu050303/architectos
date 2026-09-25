"""One owner walks the whole projects and requirements flow through the public API, the way the
frontend will: project, requirements in convenient notation, AI suggestions, analysis, a
conflict resolved, requirement sets and their planning input, history, archive, delete."""

import hashlib
import json
from typing import Any

import pytest
from httpx import AsyncClient

from apps.api.email.transport import InMemoryTransport

from .requirement_support import WEB, signed_in

pytestmark = pytest.mark.integration


async def test_the_full_journey(client: AsyncClient, outbox: InMemoryTransport) -> None:  # noqa: PLR0915 - one story
    ada = await signed_in(client, outbox, "ada@example.com")

    async def call(method: str, url: str, status: int, body: dict[str, Any] | None = None) -> Any:
        response = await client.request(method, f"/api/v1{url}", json=body, headers=ada | WEB)
        assert response.status_code == status, (method, url, response.text)
        return response.json() if response.content else None

    # An organization and a project with engine settings.
    org = await call("POST", "/organizations", 201, {"name": "Acme"})
    project = await call(
        "POST",
        f"/organizations/{org['id']}/projects",
        201,
        {"name": "Food Delivery Platform", "settings": {"cloudProvider": "aws", "currency": "EUR"}},
    )
    assert project["slug"] == "food-delivery-platform"
    base = f"/projects/{project['id']}/requirements"

    # Requirements, written the way people write them.
    throughput = await call(
        "POST",
        base,
        201,
        {
            "type": "capacity",
            "category": "throughput",
            "title": "Peak ordering traffic",
            "statement": "Handle 2k requests/sec at dinner peak.",
            "priority": "critical",
            "status": "active",
            "structuredData": {"metric": "rps", "operator": ">=", "quantity": "2k requests/sec"},
        },
    )
    assert throughput["normalizedData"]["value"] == "2000"
    latency = await call(
        "POST",
        base,
        201,
        {
            "type": "performance",
            "category": "latency",
            "title": "Checkout latency",
            "statement": "Checkout p95 under 300 ms.",
            "priority": "high",
            "status": "active",
            "structuredData": {
                "metric": "latency",
                "operator": "<=",
                "quantity": "300 ms",
                "percentile": "p95",
            },
        },
    )
    # An AI suggestion arrives as a draft with its confidence, and contradicts the traffic target.
    suggestion = await call(
        "POST",
        base,
        201,
        {
            "type": "capacity",
            "category": "throughput",
            "title": "Budget traffic cap",
            "statement": "Cap traffic at 1,500 requests per second to stay in budget.",
            "priority": "medium",
            "source": "ai",
            "confidence": "0.62",
            "structuredData": {
                "metric": "requests_per_second",
                "operator": "<=",
                "value": "1500",
                "unit": "rps",
            },
        },
    )
    assert (suggestion["status"], suggestion["confidence"]) == ("draft", "0.62")

    analysis = await call("GET", f"{base}/analysis", 200)
    assert [c["requirements"][1]["reference"] for c in analysis["conflicts"]] == [suggestion["reference"]]
    assert {"availability", "retention"} <= set(analysis["completeness"]["missing"])
    assert {
        "requirement": {"id": suggestion["id"], "reference": "REQ-3", "version": 1},
        "reason": "low_confidence",
    } in (analysis["ambiguous"])

    # The team rejects the suggestion and fills a gap.
    await call("PATCH", f"{base}/{suggestion['id']}", 200, {"expectedVersion": 1, "status": "deprecated"})
    await call(
        "POST",
        base,
        201,
        {
            "type": "availability",
            "category": "availability",
            "title": "Uptime",
            "statement": "99.9% monthly availability.",
            "priority": "critical",
            "status": "active",
            "structuredData": {"metric": "availability", "operator": ">=", "quantity": "99.9%"},
        },
    )
    analysis = await call("GET", f"{base}/analysis", 200)
    assert analysis["conflicts"] == []
    assert "availability" not in analysis["completeness"]["missing"]

    # Requirement Set v1: what the first architecture will be planned against.
    v1 = await call("POST", f"/projects/{project['id']}/requirement-sets", 201, {"name": "MVP"})
    assert [r["reference"] for r in v1["requirements"]] == ["REQ-1", "REQ-2", "REQ-4"]
    planning = await call("GET", f"/projects/{project['id']}/requirement-sets/{v1['id']}/planning-input", 200)
    document = planning["planningInput"]
    assert document["project"]["settings"] == {"cloud_provider": "aws", "currency": "EUR"}
    assert [r["constraint"]["unit"] for r in document["requirements"]] == ["requests/second", "ms", "ratio"]
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert hashlib.sha256(canonical).hexdigest() == v1["contentHash"]

    # The forecast grows: a new version, a new set, and v1 still says what it said.
    await call(
        "PATCH",
        f"{base}/{throughput['id']}",
        200,
        {
            "expectedVersion": 1,
            "structuredData": {"metric": "rps", "operator": ">=", "quantity": "5k rps"},
            "changeReason": "Traffic forecast increased from 2K to 5K RPS",
        },
    )
    v2 = await call("POST", f"/projects/{project['id']}/requirement-sets", 201, {"name": "Scale-up"})
    assert v2["contentHash"] != v1["contentHash"]
    assert v2["requirements"][0] == {"requirementId": throughput["id"], "reference": "REQ-1", "version": 2}
    again = await call("GET", f"/projects/{project['id']}/requirement-sets/{v1['id']}/planning-input", 200)
    assert again == planning
    history = await call("GET", f"{base}/{throughput['id']}/versions", 200)
    assert [(v["version"], v["normalizedData"]["value"], v["changeReason"]) for v in history["versions"]] == [
        (1, "2000", None),
        (2, "5000", "Traffic forecast increased from 2K to 5K RPS"),
    ]

    # Archived: everything readable, nothing writable; restored: writable again.
    await call("POST", f"/projects/{project['id']}/archive", 200)
    await call(
        "PATCH", f"{base}/{latency['id']}", 409, {"expectedVersion": 1, "title": "x", "changeReason": "x"}
    )
    await call("POST", f"/projects/{project['id']}/requirement-sets", 409, {})
    await call("GET", f"{base}/{latency['id']}", 200)
    await call("POST", f"/projects/{project['id']}/restore", 200)
    await call(
        "PATCH",
        f"{base}/{latency['id']}",
        200,
        {"expectedVersion": 1, "priority": "critical", "changeReason": "SLA"},
    )

    # The audit trail tells the story, newest first, without requirement text.
    trail = await call("GET", f"/organizations/{org['id']}/audit-log?limit=100", 200)
    actions = [e["action"] for e in reversed(trail["entries"])]
    assert actions[:3] == ["organization.created", "project.created", "requirement.created"]
    assert actions.count("requirement_set.created") == 2
    assert {"project.archived", "project.restored", "requirement.status_changed"} <= set(actions)
    assert "Checkout p95" not in json.dumps(trail)

    # Finally, retire the project: archive, delete, gone.
    await call("POST", f"/projects/{project['id']}/archive", 200)
    await call("DELETE", f"/projects/{project['id']}", 204)
    await call("GET", f"/projects/{project['id']}", 404)
    await call("GET", base, 404)
