"""Deterministic step generation (Migration Planning, phase 4): steps from the actual changes, the chosen
strategy and the templates — each traced to its changes and pattern, with preconditions, inputs,
outcome and completion criteria in words; explicit dependencies; stateful replacements carried out
by replication or offline with the downtime stated; unsupported or ambiguous changes left as
findings, never invented steps; nothing executed, nothing modified."""

import dataclasses
import json
import re
from typing import Any

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import to_dict
from core.domain.migrations.plans import MigrationProposal
from core.domain.migrations.steps import MigrationStep, step_id
from core.domain.migrations.values import DowntimeStatus, FindingType, PlanStatus, Reversibility, TraceKind
from engines.migration.patternbook import default_registry
from engines.migration.planner import generate
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.migration.test_migration_changes import SOURCE_IR, changed, original, shop
from tests.unit.migration.test_migration_patterns import context, mysql

REGISTRY = default_registry()
COMMANDS = re.compile(r"\b(kubectl|terraform|gcloud|psql|mysqldump|sudo|ssh|curl|aws\s)|\$ ", re.IGNORECASE)


def plan(
    after: ArchitectureIR, strategy: str | None = None, source_ir: ArchitectureIR = SOURCE_IR
) -> MigrationProposal:
    return generate(context(after, source_ir, strategy), REGISTRY)


def steps(proposal: MigrationProposal) -> dict[str, MigrationStep]:
    return {s.key: s for s in proposal.steps}


def depends(proposal: MigrationProposal, key: str) -> set[str]:
    by_id = {s.id: s.key for s in proposal.steps}
    return {by_id[i] for i in steps(proposal)[key].depends_on}


def test_a_configuration_change_is_applied_then_verified_in_place() -> None:
    proposal = plan(shop(api=changed("api", replicas=4)))
    assert (proposal.strategy, set(steps(proposal))) == ("in_place", {"configure:api", "verify:api"})
    configure = steps(proposal)["configure:api"]
    assert configure.completion == ("configuration.replicas is 4.",)
    assert configure.downtime is DowntimeStatus.UNKNOWN  # not modeled: never claimed online
    assert {t.kind for t in configure.traces} == {TraceKind.CHANGE, TraceKind.PATTERN}
    assert depends(proposal, "verify:api") == {"configure:api"}
    assert proposal.status is PlanStatus.DRAFT


def test_a_supported_rolling_strategy_is_followed() -> None:
    proposal = plan(shop(api=changed("api", replicas=4, health_check=True)), strategy="rolling")
    configure = steps(proposal)["configure:api"]
    assert proposal.strategy == "rolling"
    assert configure.title.startswith("Roll out")
    assert configure.downtime is DowntimeStatus.MODELED_ONLINE
    assert any(t.reference == "rolling@1" for t in configure.traces)


def test_an_unsupported_preference_falls_back_to_in_place_with_a_finding() -> None:
    proposal = plan(shop(api=changed("api", replicas=4)), strategy="rolling")  # no health check declared
    assert proposal.strategy == "in_place"
    assert [f.type for f in proposal.findings] == [FindingType.STRATEGY_NOT_SUPPORTED]
    assert steps(proposal)["configure:api"].title.startswith("Apply")


def routed(**nodes: Any) -> ArchitectureIR:
    return dataclasses.replace(
        shop(lb=node("lb", NodeKind.LOAD_BALANCER), **nodes),
        connections=(
            connection("web-lb", "web", "lb", kind=ConnectionKind.REQUEST, protocol="https"),
            connection("lb-api", "lb", "api", kind=ConnectionKind.REQUEST, protocol="https"),
            connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql"),
        ),
    )


def test_a_blue_green_switch_keeps_the_previous_environment_until_verified() -> None:
    proposal = plan(routed(api=changed("api", replicas=4)), strategy="blue_green", source_ir=routed())
    assert proposal.strategy == "blue_green"
    assert depends(proposal, "cutover:api") == {"verify:api:green"}
    assert depends(proposal, "decommission:api:blue") == {"verify:api"}
    assert steps(proposal)["cutover:api"].manual_verification


def test_a_new_component_is_provisioned_and_verified_before_it_is_connected() -> None:
    after = dataclasses.replace(
        shop(cache=node("cache", NodeKind.CACHE)),
        connections=(*SOURCE_IR.connections, connection("api-cache", "api", "cache", protocol="redis")),
    )
    proposal = plan(after)
    expected = {
        "provision:cache",
        "configure:cache",
        "verify:cache",
        "provision:api-cache",
        "verify:api-cache",
    }
    assert set(steps(proposal)) == expected
    assert depends(proposal, "provision:api-cache") == {"verify:cache"}  # after its new endpoint


def test_a_removed_component_is_decommissioned_after_its_connections() -> None:
    after = ArchitectureIR(
        "Shop",
        nodes=(original("web"), original("api")),
        connections=tuple(c for c in SOURCE_IR.connections if c.id == "web-api"),
    )
    proposal = plan(after)
    assert depends(proposal, "verify:db:unused") == {"decommission:api-db"}
    assert depends(proposal, "decommission:db") == {"verify:db:unused"}
    db = steps(proposal)["decommission:db"]
    assert (db.reversibility, db.manual_verification) == (Reversibility.IRREVERSIBLE, True)
    assert db.data_impact


REPLACEMENT = {
    "provision:db:target", "backfill:db", "verify:db:consistency", "cutover:db", "verify:db:traffic",
    "decommission:db:source",
}  # fmt: skip


def test_a_stateful_technology_change_moves_its_data_offline_by_default() -> None:
    proposal = plan(mysql())
    assert set(steps(proposal)) == {*REPLACEMENT, "prepare:db:freeze"}  # never merely reconfigured
    assert steps(proposal)["prepare:db:freeze"].downtime is DowntimeStatus.KNOWN_DOWNTIME  # stated
    assert steps(proposal)["cutover:db"].downtime is DowntimeStatus.KNOWN_DOWNTIME
    assert steps(proposal)["decommission:db:source"].reversibility is Reversibility.IRREVERSIBLE
    assert depends(proposal, "cutover:db") == {"verify:db:consistency"}
    assert depends(proposal, "decommission:db:source") == {"verify:db:traffic"}


def test_a_declared_replication_is_followed_by_a_cutover() -> None:
    proposal = plan(mysql(replication_mode="asynchronous"), strategy="replication_cutover")
    assert proposal.strategy == "replication_cutover"
    assert set(steps(proposal)) == {*REPLACEMENT, "replicate:db"}
    cutover = steps(proposal)["cutover:db"]
    assert (cutover.downtime, bool(cutover.downtime_note)) == (DowntimeStatus.POTENTIAL_DOWNTIME, True)
    assert depends(proposal, "backfill:db") == {"replicate:db"}
    assert any(t.reference == "replication_cutover@1" for t in cutover.traces)


def test_a_replacement_switches_clients_before_the_old_component_is_retired() -> None:
    db2 = node("db2", NodeKind.DATABASE, technology=Technology("mysql", "8"))
    after = ArchitectureIR(
        "Shop",
        nodes=(*(n for n in SOURCE_IR.nodes if n.id != "db"), db2),
        connections=(
            *(c for c in SOURCE_IR.connections if c.id == "web-api"),
            connection("api-db2", "api", "db2", kind=ConnectionKind.DATA_ACCESS, protocol="mysql"),
        ),
    )
    proposal = plan(after)
    assert depends(proposal, "provision:api-db2") == {"verify:db2"}
    assert depends(proposal, "cutover:db") == {"verify:db:consistency", "verify:api-db2"}
    assert "cutover:db" in depends(proposal, "verify:api-db:unused")
    assert {"cutover:db", "verify:db2:traffic", "decommission:api-db"} <= depends(
        proposal, "verify:db:unused"
    )
    assert depends(proposal, "decommission:db") == {"verify:db:unused"}


def test_unsupported_and_ambiguous_changes_are_findings_not_steps() -> None:
    worker = dataclasses.replace(original("api"), kind=NodeKind.WORKER)
    proposal = plan(shop(api=worker, db=changed("db", region="us-east-1")))
    assert proposal.steps == ()
    assert {f.type for f in proposal.findings} == {
        FindingType.UNSUPPORTED_CHANGE,
        FindingType.MANUAL_INTERPRETATION,
    }
    assert (proposal.status, proposal.strategy) == (PlanStatus.NEEDS_INFORMATION, None)


def test_metadata_only_changes_plan_nothing() -> None:
    proposal = plan(dataclasses.replace(SOURCE_IR, description="Renamed."))
    assert proposal.steps == ()
    assert [f.type for f in proposal.findings] == [FindingType.NO_CHANGES]


def test_steps_are_traced_deterministic_and_never_commands() -> None:
    after = shop(
        api=changed("api", replicas=4),
        db=changed("db", encryption_at_rest=True),
        cache=node("cache", NodeKind.CACHE),
    )
    first, again = plan(after), plan(after)
    assert first.to_dict() == again.to_dict()
    assert first.fingerprint == again.fingerprint
    known = {s.id for s in first.steps}
    for step in first.steps:
        assert step.id == step_id(step.key)
        assert set(step.depends_on) <= known
        assert {t.kind for t in step.traces} == {TraceKind.CHANGE, TraceKind.PATTERN}
        assert step.completion
        assert not COMMANDS.search(json.dumps(step.to_dict()))  # words, never commands
    assert {"migration_planner", "in_place", "rolling"} <= set(first.models)
    assert [o.pattern for o in first.alternatives] == sorted(o.pattern for o in first.alternatives)


def test_nothing_records_an_execution() -> None:
    text = json.dumps(plan(mysql()).to_dict())
    assert not re.search(r'"(executed|executed_at|completed_at|started_at|done|applied)"', text)


def test_generation_changes_neither_architecture() -> None:
    after = mysql(replication_mode="asynchronous")
    before = (json.dumps(to_dict(SOURCE_IR)), json.dumps(to_dict(after)))
    plan(after, strategy="replication_cutover")
    assert (json.dumps(to_dict(SOURCE_IR)), json.dumps(to_dict(after))) == before
