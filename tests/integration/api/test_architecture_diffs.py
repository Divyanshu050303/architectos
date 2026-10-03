"""The architecture diff API against a real database (AI Architecture Diff, phase 6), with a scripted
model in place of a provider: two revisions compare deterministically and are stored; an explanation is
appended, never changing the diff; identical states need none (no model call); an agent run's candidate
compares; a missing, hidden or non-comparable state gets one answer; capacity is compared only on a
named analysis; a refused or unconfigured explanation is said, never invented; a secret's values are
never stored or returned; diffs are authorized per project and tenant-isolated; both tables are
append-only."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from engines.architecture_diff.interpreter import build_interpreter
from persistence.models import ArchitectureDiffRecord, DiffExplanationRecord

from .diff_support import ContextLlm, scripted_interpreter
from .requirement_support import World, member, signed_in
from .test_architecture_agent import ready

pytestmark = pytest.mark.integration

SECRET_BEFORE, SECRET_AFTER = "s3cr3t-before-value", "s3cr3t-after-value"
SHOP: dict[str, Any] = {
    "schema_version": 1,
    "name": "Shop",
    "nodes": [
        {"id": "web", "kind": "client", "name": "Web"},
        {
            "id": "api",
            "kind": "service",
            "name": "Orders API",
            "configuration": {"values": {"replicas": 2}, "extra": {"api_key": SECRET_BEFORE}},
        },
        {"id": "db", "kind": "database", "name": "Orders DB"},
    ],
    "connections": [
        {"id": "web-api", "source_id": "web", "target_id": "api", "kind": "request", "protocol": "https"},
        {
            "id": "api-db",
            "source_id": "api",
            "target_id": "db",
            "kind": "data_access",
            "protocol": "postgresql",
        },
    ],
}
WORKLOAD = {"name": "Peak", "type": "request_response", "peakRate": {"value": 100, "unit": "requests/second"}}


def diffs(world: World, project_id: str | None = None) -> str:
    return f"/api/v1/projects/{project_id or world.project_id}/architecture-diffs"


def revision(architecture_id: str, number: int) -> dict[str, Any]:
    return {"kind": "revision", "architectureId": architecture_id, "revisionNumber": number}


async def architecture(
    client: AsyncClient, world: World, ir: dict[str, Any] = SHOP, name: str = "Shop"
) -> str:
    created = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures", json={"name": name, "ir": ir}, headers=world.ada
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def scaled(client: AsyncClient, world: World) -> str:
    """An architecture with revision 2: the API scaled to 4 replicas."""
    architecture_id = await architecture(client, world)
    edited = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures/{architecture_id}/commands",
        json={"baseVersion": 1, "commands": [{"type": "change_replicas", "nodeId": "api", "replicas": 4}]},
        headers=world.ada,
    )
    assert edited.status_code == 201, edited.text
    return architecture_id


async def compare(client: AsyncClient, world: World, base: Any, target: Any, **body: Any) -> Any:
    response = await client.post(
        diffs(world), json={"base": base, "target": target} | body, headers=world.ada
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- comparing ---------------------------------------------------------------------------------------


async def test_two_revisions_compare_deterministically(client: AsyncClient, world: World) -> None:
    architecture_id = await scaled(client, world)
    diff = await compare(client, world, revision(architecture_id, 1), revision(architecture_id, 2))
    [change] = diff["changes"]
    assert (change["elementId"], change["change"]) == ("api", "modified")
    fields = {f["path"]: f for f in change["fields"]}
    assert set(fields) == {"configuration.replicas", "field_provenance.configuration.replicas"}
    assert (fields["configuration.replicas"]["before"], fields["configuration.replicas"]["after"]) == (2, 4)
    assert "scaling" in change["classes"]
    assert sorted(i for g in diff["groups"] for i in g["changeIds"]) == [change["id"]]
    engines = {e["engine"]: e for e in diff["engines"]}
    assert set(engines) == {"validation", "reliability", "security", "observability", "capacity", "cost"}
    for name in ("validation", "reliability", "security", "observability"):
        assert engines[name]["status"] == "evaluated", engines[name]
    for name in ("capacity", "cost"):  # no analysis named: not estimated
        assert engines[name]["status"] == "not_evaluated"
        assert engines[name]["limitations"]
    assert diff["warnings"] == []  # the same architecture
    assert diff["explanations"] == []
    again = await compare(client, world, revision(architecture_id, 1), revision(architecture_id, 2))
    assert again["changes"] == diff["changes"]
    assert again["groups"] == diff["groups"]

    read = await client.get(f"{diffs(world)}/{diff['id']}", headers=world.ada)
    assert read.status_code == 200
    assert read.json() == diff
    listed = (await client.get(diffs(world), headers=world.ada)).json()
    assert [d["id"] for d in listed["diffs"]] == [again["id"], diff["id"]]
    assert listed["diffs"][0]["changeCount"] == 1
    other = await architecture(client, world, name="Other")
    narrowed = await client.get(diffs(world), params={"architectureId": other}, headers=world.ada)
    assert narrowed.json()["diffs"] == []


async def test_a_secret_change_is_reported_never_stored_or_shown(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    architecture_id = await architecture(client, world)
    rotated = SHOP | {
        "nodes": [
            n | {"configuration": {"values": {"replicas": 2}, "extra": {"api_key": SECRET_AFTER}}}
            if n["id"] == "api"
            else n
            for n in SHOP["nodes"]
        ]
    }
    replaced = await client.put(
        f"/api/v1/projects/{world.project_id}/architectures/{architecture_id}/content",
        json={"baseVersion": 1, "ir": rotated},
        headers=world.ada,
    )
    assert replaced.status_code == 201, replaced.text
    diff = await compare(
        client, world, revision(architecture_id, 1), revision(architecture_id, 2), explain=True
    )
    [field] = diff["changes"][0]["fields"]
    assert field["sensitivity"] == "secret"
    assert (field["beforeType"], field["afterType"]) == ("redacted", "redacted")
    stored = await db.scalar(select(ArchitectureDiffRecord))
    assert stored is not None
    for value in (SECRET_BEFORE, SECRET_AFTER):
        assert value not in repr(diff)
        assert value not in repr(stored.semantic)


async def test_identical_states_need_no_explanation(app: FastAPI, client: AsyncClient, world: World) -> None:
    llm = ContextLlm()
    app.state.diff_interpreter = scripted_interpreter(llm)
    first = await architecture(client, world, name="Shop A")
    second = await architecture(client, world, name="Shop B")  # the same content
    diff = await compare(client, world, revision(first, 1), revision(second, 1), explain=True)
    assert diff["identical"] is True
    assert diff["changes"] == []
    assert diff["warnings"]  # different architectures: matched by element id alone
    [run] = diff["explanations"]
    assert run["status"] == "not_needed"
    assert run["model"] is None
    assert llm.requests == []


async def test_an_agent_candidate_compares_with_a_revision(client: AsyncClient, world: World) -> None:
    run = await ready(client, world)
    architecture_id = await architecture(client, world)
    candidate = {"kind": "candidate", "runId": run["id"]}
    diff = await compare(client, world, revision(architecture_id, 1), candidate)
    assert diff["target"]["contentHash"] == run["candidate"]["contentHash"]
    assert diff["target"]["kind"] == "candidate"
    assert {c["change"] for c in diff["changes"]} >= {"added", "removed"}
    assert diff["warnings"]  # a candidate without a base is another architecture


async def test_missing_hidden_and_uncomparable_states_get_one_answer(
    client: AsyncClient, world: World
) -> None:
    architecture_id = await scaled(client, world)
    elsewhere = await client.post(
        f"/api/v1/projects/{world.other_id}/architectures",
        json={"name": "Pay", "ir": SHOP},
        headers=world.ada,
    )
    foreign = str(elsewhere.json()["id"])
    unknown_run = {"kind": "candidate", "runId": "00000000-0000-7000-8000-000000000001"}
    for target in (revision(architecture_id, 9), revision(foreign, 1), unknown_run):
        response = await client.post(
            diffs(world), json={"base": revision(architecture_id, 1), "target": target}, headers=world.ada
        )
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "compared_state_not_found"
        assert response.json()["error"]["details"] == {"side": "target"}
    same = await client.post(
        diffs(world),
        json={"base": revision(architecture_id, 1), "target": revision(architecture_id, 1)},
        headers=world.ada,
    )
    assert same.status_code == 422
    assert same.json()["error"]["code"] == "invalid_diff_request"
    assert (await client.get(diffs(world), headers=world.ada)).json()["diffs"] == []


async def test_capacity_is_compared_only_on_a_named_analysis(client: AsyncClient, world: World) -> None:
    architecture_id = await scaled(client, world)
    analysis = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures/{architecture_id}/capacity-analyses",
        json={"workload": WORKLOAD},
        headers=world.ada,
    )
    assert analysis.status_code == 201, analysis.text
    diff = await compare(
        client,
        world,
        revision(architecture_id, 1),
        revision(architecture_id, 2),
        capacityAnalysisId=analysis.json()["id"],
    )
    capacity = next(e for e in diff["engines"] if e["engine"] == "capacity")
    assert capacity["status"] == "evaluated", capacity
    assert any("workload of capacity analysis" in limit for limit in capacity["limitations"])
    unknown = await client.post(
        diffs(world),
        json={
            "base": revision(architecture_id, 1),
            "target": revision(architecture_id, 2),
            "capacityAnalysisId": "00000000-0000-7000-8000-000000000002",
        },
        headers=world.ada,
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "capacity_analysis_not_found"


# --- explaining ---------------------------------------------------------------------------------------


async def test_explanations_are_appended_and_never_change_the_diff(
    app: FastAPI, client: AsyncClient, world: World
) -> None:
    llm = ContextLlm()
    app.state.diff_interpreter = scripted_interpreter(llm)
    architecture_id = await scaled(client, world)
    diff = await compare(
        client,
        world,
        revision(architecture_id, 1),
        revision(architecture_id, 2),
        explain=True,
        context="Black Friday is coming.",
    )
    [first] = diff["explanations"]
    assert first["status"] == "completed", first
    assert first["explanation"]["summary"]["groundings"] == [
        {"basis": "change", "ref": diff["changes"][0]["id"]}
    ]
    assert first["explanation"]["risks"][0]["inferred"] is True
    assert first["usage"]["modelCalls"] == 1
    assert first["promptVersion"] == "diff-explanation-v1"
    [sent] = llm.requests
    assert "Black Friday is coming." in sent.user_content  # the person's words, as data
    assert "Black Friday" not in sent.system

    second = await client.post(f"{diffs(world)}/{diff['id']}/explanations", headers=world.ada)
    assert second.status_code == 201, second.text
    read = (await client.get(f"{diffs(world)}/{diff['id']}", headers=world.ada)).json()
    assert [r["id"] for r in read["explanations"]] == [first["id"], second.json()["id"]]
    assert {k: v for k, v in read.items() if k != "explanations"} == {
        k: v for k, v in diff.items() if k != "explanations"
    }
    listed = (await client.get(diffs(world), headers=world.ada)).json()
    assert listed["diffs"][0]["explanations"] == 2


async def test_a_refused_explanation_is_said_never_shown(
    app: FastAPI, client: AsyncClient, db: AsyncSession, world: World
) -> None:
    app.state.diff_interpreter = scripted_interpreter(ContextLlm(unknowns=["See https://evil.example now."]))
    architecture_id = await scaled(client, world)
    diff = await compare(
        client, world, revision(architecture_id, 1), revision(architecture_id, 2), explain=True
    )
    [run] = diff["explanations"]
    assert run["status"] == "failed"
    assert run["failure"] == "explanation_rejected"
    assert run["explanation"] is None
    assert {r["code"] for r in run["rejections"]} == {"url_in_output"}
    assert run["usage"]["modelCalls"] == 1  # refused, not asked again
    stored = await db.scalar(select(DiffExplanationRecord))
    assert stored is not None
    assert stored.raw_output_sha256 is not None  # a hash, never the text
    assert "evil.example" not in repr(stored.__dict__)


async def test_without_a_model_an_explanation_fails_llm_unavailable(
    app: FastAPI, client: AsyncClient, world: World
) -> None:
    app.state.diff_interpreter = build_interpreter(
        provider="none", api_key=None, model="unused", timeout_seconds=10
    )
    architecture_id = await scaled(client, world)
    diff = await compare(client, world, revision(architecture_id, 1), revision(architecture_id, 2))
    run = await client.post(f"{diffs(world)}/{diff['id']}/explanations", headers=world.ada)
    assert run.status_code == 201
    assert run.json()["status"] == "failed"
    assert run.json()["failure"] == "llm_unavailable"
    assert run.json()["usage"]["modelCalls"] == 0


# --- access, isolation, the record -------------------------------------------------------------------


async def test_a_viewer_reads_but_does_not_compare_or_explain(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    architecture_id = await scaled(client, world)
    diff = await compare(client, world, revision(architecture_id, 1), revision(architecture_id, 2))
    viewer = await member(client, db, outbox, world, "viewer")
    assert (await client.get(f"{diffs(world)}/{diff['id']}", headers=viewer)).status_code == 200
    body = {"base": revision(architecture_id, 1), "target": revision(architecture_id, 2)}
    assert (await client.post(diffs(world), json=body, headers=viewer)).status_code == 403
    explained = await client.post(f"{diffs(world)}/{diff['id']}/explanations", headers=viewer)
    assert explained.status_code == 403


async def test_diffs_of_other_projects_and_tenants_are_not_found(
    client: AsyncClient, outbox: InMemoryTransport, world: World
) -> None:
    architecture_id = await scaled(client, world)
    diff = await compare(client, world, revision(architecture_id, 1), revision(architecture_id, 2))
    elsewhere = await client.get(f"{diffs(world, world.other_id)}/{diff['id']}", headers=world.ada)
    assert elsewhere.status_code == 404
    assert elsewhere.json()["error"]["code"] == "architecture_diff_not_found"
    explained = await client.post(
        f"{diffs(world, world.other_id)}/{diff['id']}/explanations", headers=world.ada
    )
    assert explained.status_code == 404
    stranger = await signed_in(client, outbox, "mallory@example.com")
    assert (await client.get(f"{diffs(world)}/{diff['id']}", headers=stranger)).status_code == 404


async def test_unknown_fields_are_refused(client: AsyncClient, world: World) -> None:
    architecture_id = await scaled(client, world)
    body = {"base": revision(architecture_id, 1), "target": revision(architecture_id, 2), "score": 10}
    assert (await client.post(diffs(world), json=body, headers=world.ada)).status_code == 422
    mixed = {"kind": "revision", "runId": "00000000-0000-7000-8000-000000000003"}
    body = {"base": mixed, "target": revision(architecture_id, 2)}
    refused = await client.post(diffs(world), json=body, headers=world.ada)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "invalid_diff_request"


async def test_the_stored_record_is_append_only(client: AsyncClient, db: AsyncSession, world: World) -> None:
    architecture_id = await scaled(client, world)
    await compare(client, world, revision(architecture_id, 1), revision(architecture_id, 2), explain=True)
    assert await db.scalar(select(func.count()).select_from(ArchitectureDiffRecord)) == 1
    assert await db.scalar(select(func.count()).select_from(DiffExplanationRecord)) == 1
    for table in ("architecture_diffs", "architecture_diff_explanations"):
        for statement in (
            f"UPDATE {table} SET requested_by_user_id = requested_by_user_id",  # noqa: S608 - fixed names
            f"DELETE FROM {table}",  # noqa: S608
            f"TRUNCATE {table} CASCADE",
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                async with db.begin_nested():
                    await db.execute(text(statement))
