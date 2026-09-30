"""Data migration, downtime and compatibility (Migration Planning, phase 6): data moves described with
what is missing (never a volume, throughput or duration), downtime classified and held against the
stated constraints (never a zero-downtime claim), and compatibility questions traced to their
changes and unknown until evidence establishes them."""

import dataclasses
import json
import re
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain.migrations.entities import (
    DataRequirement,
    MigrationConstraints,
    MigrationRequest,
    TargetSpec,
)
from core.domain.migrations.errors import InvalidMigrationPlan
from core.domain.migrations.plans import MigrationProposal
from core.domain.migrations.steps import CompatibilityAspect, CompatibilityCheck, MigrationStep, Trace
from core.domain.migrations.values import (
    CompatibilityStatus,
    DowntimeStatus,
    FindingType,
    PlanStatus,
    TraceKind,
)
from engines.migration.changes import analyze, revision_target
from engines.migration.patternbook import default_registry
from engines.migration.patterns import PlanningContext
from engines.migration.planner import generate
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.migration.test_migration_changes import (
    ARCHITECTURE,
    SOURCE,
    SOURCE_IR,
    changed,
    original,
    shop,
)
from tests.unit.migration.test_migration_patterns import mysql
from tests.unit.migration.test_migration_sequencing import replaced, without_db
from tests.unit.migration.test_migration_steps import routed

ALLOWED = MigrationConstraints(downtime_allowed=True)
KEEP = (DataRequirement("db", "All orders and customers; retained until the audit closes."),)
FABRICATED = re.compile(r"\b\d+(\.\d+)?\s*(ms|s|sec|seconds?|minutes?|mins?|hours?|h|days?|[KMGT]i?B|%)\b")
ONLINE_OR_UNKNOWN = {DowntimeStatus.UNKNOWN, DowntimeStatus.MODELED_ONLINE}


def plan(
    after: ArchitectureIR, strategy: str | None = None, source_ir: ArchitectureIR = SOURCE_IR, **request: Any
) -> MigrationProposal:
    source = (
        SOURCE
        if source_ir is SOURCE_IR
        else dataclasses.replace(SOURCE, content_hash=content_hash(source_ir))
    )
    analysis = analyze(source_ir, after, source, revision_target(source, 2, content_hash(after)))
    stated = MigrationRequest(ARCHITECTURE, 1, TargetSpec(revision=2), strategy=strategy, **request)
    return generate(PlanningContext(source_ir, after, analysis, stated), default_registry())


def steps(proposal: MigrationProposal) -> dict[str, MigrationStep]:
    return {s.key: s for s in proposal.steps}


def findings(proposal: MigrationProposal) -> dict[str, FindingType]:
    return {f.key: f.type for f in proposal.findings}


# --- data migration --------------------------------------------------------------------------------


def test_an_offline_move_states_its_data_requirements_and_what_is_missing() -> None:
    proposal = plan(mysql(), constraints=ALLOWED)
    [data] = proposal.data_migrations
    assert (data.key, data.source_element_id, data.destination_element_id) == ("data:db", "db", "db")
    assert data.method is not None
    assert data.method.startswith("An offline copy")
    assert data.replication is None
    assert all((data.backfill, data.verification, data.cutover, data.retention, data.rollback))
    assert "writes staying stopped" in (data.data_loss or "")
    assert any("volume" in m for m in data.missing)  # never invented
    assert any("scope" in m for m in data.missing)
    assert data.to_dict()["duration"] == "unevaluable"
    by_id = {s.id: s.key for s in proposal.steps}
    assert {by_id[i] for i in data.step_ids} >= {"prepare:db:freeze", "backfill:db", "cutover:db"}
    assert "provision:db:target" not in {by_id[i] for i in data.step_ids}
    assert findings(proposal) == {"data_scope:db": FindingType.MISSING_INFORMATION}
    assert proposal.status is PlanStatus.NEEDS_INFORMATION


def test_a_stated_data_requirement_is_recorded_and_traced() -> None:
    proposal = plan(mysql(), constraints=ALLOWED, data_requirements=KEEP)
    [data] = proposal.data_migrations
    assert data.scope == KEEP[0].statement
    assert not any("scope" in m for m in data.missing)
    assert any("volume" in m for m in data.missing)  # still not modeled
    assert Trace(TraceKind.CONSTRAINT, "data_requirements.db", KEEP[0].statement) in data.traces
    assert findings(proposal) == {}
    assert proposal.status is PlanStatus.DRAFT


@pytest.mark.parametrize(
    ("mode", "loss"), [("asynchronous", "lag is not modeled"), ("synchronous", "must be verified")]
)
def test_a_replicated_move_states_its_replication_and_where_data_could_be_lost(mode: str, loss: str) -> None:
    proposal = plan(mysql(replication_mode=mode), "replication_cutover", data_requirements=KEEP)
    [data] = proposal.data_migrations
    assert (data.method or "").startswith("Replication")
    assert mode in (data.replication or "")
    assert loss in (data.data_loss or "")
    assert any("caught up" in c for c in data.cutover)


def test_a_replacement_moves_data_to_the_new_component() -> None:
    proposal = plan(replaced(), constraints=ALLOWED, data_requirements=KEEP)
    [data] = proposal.data_migrations
    assert (data.source_element_id, data.destination_element_id) == ("db", "db2")


def test_a_removed_database_states_that_its_data_goes_with_it() -> None:
    proposal = plan(without_db())
    [data] = proposal.data_migrations
    assert (data.source_element_id, data.destination_element_id, data.method) == ("db", None, None)
    assert data.data_loss == "Decommissioning db removes its data unless it is retained first."
    assert findings(proposal) == {"data_retention:db": FindingType.MISSING_INFORMATION}
    assert findings(plan(without_db(), data_requirements=KEEP)) == {}


def test_stateless_changes_move_no_data() -> None:
    assert plan(shop(api=changed("api", replicas=4))).data_migrations == ()


# --- downtime and availability ---------------------------------------------------------------------


def test_an_in_place_change_to_a_single_replica_may_cause_downtime() -> None:
    proposal = plan(shop(db=changed("db", encryption_at_rest=True)), constraints=ALLOWED)
    configure = steps(proposal)["configure:db"]
    assert configure.downtime is DowntimeStatus.POTENTIAL_DOWNTIME
    assert "Whether it restarts is not modeled" in (configure.downtime_note or "")
    assert configure.manual_verification  # a person verifies what may take it out of service


def test_declared_replicas_without_a_rolling_strategy_leave_downtime_unknown() -> None:
    api = steps(plan(shop(api=changed("api", replicas=4))))["configure:api"]
    assert api.downtime is DowntimeStatus.UNKNOWN  # never read as online
    assert any("applied in place" in a for a in api.availability)
    undeclared = shop(api=dataclasses.replace(original("api"), configuration=Configuration({"logs": True})))
    after = shop(api=dataclasses.replace(original("api"), configuration=Configuration({"logs": False})))
    api = steps(plan(after, source_ir=undeclared))["configure:api"]
    assert any("not declared" in a for a in api.availability)


def test_online_steps_rest_on_declared_prerequisites() -> None:
    rolling = steps(plan(shop(api=changed("api", replicas=4, health_check=True)), "rolling"))["configure:api"]
    assert rolling.downtime is DowntimeStatus.MODELED_ONLINE
    assert "one instance" in (rolling.traffic or "").lower()
    added = plan(
        dataclasses.replace(
            shop(cache=node("cache", NodeKind.CACHE)),
            connections=(*SOURCE_IR.connections, connection("api-cache", "api", "cache", protocol="redis")),
        )
    )
    assert steps(added)["provision:cache"].traffic == "None: nothing routes to cache yet."
    assert steps(added)["provision:api-cache"].downtime is DowntimeStatus.UNKNOWN  # its client may restart


def test_known_downtime_states_its_traffic_implications() -> None:
    proposal = plan(mysql(), constraints=ALLOWED, data_requirements=KEEP)
    freeze = steps(proposal)["prepare:db:freeze"]
    assert freeze.downtime is DowntimeStatus.KNOWN_DOWNTIME
    assert (
        freeze.traffic == "Writes to db are stopped; how its clients (api) behave meanwhile is not modeled."
    )
    assert steps(proposal)["cutover:db"].traffic


def test_downtime_is_held_against_the_stated_constraints() -> None:
    unstated = plan(mysql(), data_requirements=KEEP)
    assert findings(unstated) == {"downtime_allowed": FindingType.MISSING_INFORMATION}
    refused = plan(mysql(), constraints=MigrationConstraints(downtime_allowed=False), data_requirements=KEEP)
    conflicts = {k for k, t in findings(refused).items() if t is FindingType.CONSTRAINT_CONFLICT}
    down = {f"downtime:{s.key}" for s in refused.steps if s.downtime not in ONLINE_OR_UNKNOWN}
    assert down
    assert conflicts == down
    assert refused.status is PlanStatus.NEEDS_INFORMATION
    window = MigrationConstraints(downtime_allowed=True, maintenance_window="Sunday 02:00-04:00 UTC")
    allowed = steps(plan(mysql(), constraints=window, data_requirements=KEEP))["prepare:db:freeze"]
    stated = Trace(TraceKind.CONSTRAINT, "constraints.downtime_allowed", "Downtime is allowed, as stated.")
    assert stated in allowed.traces
    assert "Sunday 02:00-04:00 UTC; whether the step fits in it is not modeled." in " ".join(
        allowed.availability
    )


SCENARIOS = {
    "offline": lambda: plan(mysql(), constraints=ALLOWED, data_requirements=KEEP),
    "replication": lambda: plan(mysql(replication_mode="asynchronous"), "replication_cutover"),
    "replacement": lambda: plan(replaced()),
    "removed": lambda: plan(without_db()),
    "blue_green": lambda: plan(routed(api=changed("api", replicas=4)), "blue_green", routed()),
    "single": lambda: plan(shop(db=changed("db", encryption_at_rest=True))),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_no_duration_volume_or_zero_downtime_is_ever_stated(name: str) -> None:
    text = json.dumps(SCENARIOS[name]().to_dict())
    assert not FABRICATED.search(text)
    assert "zero downtime" not in text.lower()
    assert "no downtime" not in text.lower()


# --- compatibility ---------------------------------------------------------------------------------


def test_a_database_replacement_raises_every_compatibility_question() -> None:
    checks = {c.key: c for c in plan(replaced()).compatibility}
    assert set(checks) == {
        "data_model:db", "schema:db", "version:db", "clients:db", "authentication:db", "rollback:db",
    }  # fmt: skip
    assert {c.status for c in checks.values()} == {CompatibilityStatus.UNKNOWN}  # never assumed
    assert checks["data_model:db"].question == (
        "Can db's data (postgresql 16) be represented in db2 (mysql 8) without loss?"
    )
    assert "api" in checks["clients:db"].question
    for check in checks.values():
        assert {t.reference for t in check.traces} == {"node:db:removed", "node:db2:added"}
        assert check.note


def test_technology_protocol_and_strategy_changes_raise_their_questions() -> None:
    api = dataclasses.replace(original("api"), technology=Technology("python", "3.13"))
    assert {c.key for c in plan(shop(api=api)).compatibility} == {"application:api", "version:api"}
    grpc = dataclasses.replace(
        SOURCE_IR,
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="grpc"),
            *(c for c in SOURCE_IR.connections if c.id == "api-db"),
        ),
    )
    [protocol] = plan(grpc).compatibility
    assert (protocol.key, protocol.aspect) == ("protocol:web-api", CompatibilityAspect.PROTOCOL)
    assert protocol.question == "Do web and api both support grpc (from https)?"
    rolling = plan(shop(api=changed("api", replicas=4, health_check=True)), "rolling")
    assert {c.key for c in rolling.compatibility} == {"mixed_versions:api"}
    blue_green = plan(routed(api=changed("api", replicas=4)), "blue_green", routed())
    assert {c.key for c in blue_green.compatibility} == {"environments:api"}


def test_compatibility_is_verified_only_with_machine_checkable_evidence() -> None:
    change = (Trace(TraceKind.CHANGE, "node:db:modified"),)
    with pytest.raises(InvalidMigrationPlan):
        CompatibilityCheck(
            "schema:db", CompatibilityAspect.SCHEMA, CompatibilityStatus.VERIFIED, "Q?", change
        )
    evidence = (*change, Trace(TraceKind.EVIDENCE, "validation:1"))
    verified = CompatibilityCheck(
        "schema:db", CompatibilityAspect.SCHEMA, CompatibilityStatus.VERIFIED, "Q?", evidence
    )
    assert verified.status is CompatibilityStatus.VERIFIED


def test_the_summary_counts_downtime_data_and_compatibility() -> None:
    summary = plan(replaced(), constraints=ALLOWED, data_requirements=KEEP).summary()
    assert summary["data_migrations"] == 1
    assert summary["compatibility"] == {"unknown": 6}
    assert summary["downtime"]["known_downtime"] >= 1
