"""The migration planning fixtures (Migration Planning, phase 11): fifteen transitions, each planned end
to end and checked for what the plan must state — and what it must never invent — plus determinism,
persistence round-trips, bounded generation and an unchanged architecture for every one of them."""

import dataclasses
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import to_dict
from core.domain.migrations.entities import DataRequirement
from core.domain.migrations.errors import InvalidMigrationRequest
from core.domain.migrations.plans import MigrationProposal
from core.domain.migrations.serialization import proposal_from_dict
from core.domain.migrations.values import (
    CheckpointStatus,
    CompatibilityStatus,
    DowntimeStatus,
    FindingType,
    PlanStatus,
    Reversibility,
    RiskCategory,
    RiskStatus,
    StepType,
    TraceKind,
)
from core.domain.migrations.versioning import (
    CurrentState,
    Freshness,
    StaleReason,
    freshness,
    regenerate,
    reject,
    submit,
)
from engines.migration.risk import risks
from engines.migration.rollback import rollbacks
from engines.migration.sequencing import sequence
from tests.unit.architecture_ir.builders import node
from tests.unit.migration.test_migration_changes import SOURCE_IR, changed, original, shop
from tests.unit.migration.test_migration_data_downtime import ALLOWED, plan
from tests.unit.migration.test_migration_patterns import context, mysql
from tests.unit.migration.test_migration_sequencing import ANALYSIS, replaced, step
from tests.unit.migration.test_migration_steps import routed
from tests.unit.migration.test_migration_versioning import version

KEEP = (DataRequirement("db", "Every order and customer; retained until the audit closes."),)
FABRICATED = re.compile(
    r"\b\d+(\.\d+)?\s*(ms|seconds?|minutes?|hours?|days?|[KMGT]i?B|%)\b|\bscore\b|\bprobability\b"
)
AT = datetime(2026, 10, 1, 12, tzinfo=UTC)
ADA, BOB = uuid.UUID(int=10), uuid.UUID(int=11)


def keys(proposal: MigrationProposal) -> set[str]:
    return {s.key for s in proposal.steps}


def finding_types(proposal: MigrationProposal) -> set[FindingType]:
    return {f.type for f in proposal.findings}


def monolith() -> ArchitectureIR:
    """api splits: it becomes a gateway (a role change), two services appear, and a domain boundary
    groups them — what the boundary means is for a person to say."""
    return shop(
        api=dataclasses.replace(original("api"), kind=NodeKind.GATEWAY),
        orders=node("orders"),
        payments=node("payments"),
        domain=node("domain", NodeKind.BOUNDARY),
    )


FIXTURES: dict[str, Callable[[], MigrationProposal]] = {
    "01_vertical_scaling": lambda: plan(shop(api=changed("api", cpu_limit_cores=Decimal(2)))),
    "02_database_replacement": lambda: plan(replaced(), constraints=ALLOWED, data_requirements=KEEP),
    "03_missing_data_volume": lambda: plan(mysql(), constraints=ALLOWED, data_requirements=KEEP),
    "04_replication": lambda: plan(
        mysql(replication_mode="asynchronous"),
        "replication_cutover",
        constraints=ALLOWED,
        data_requirements=KEEP,
    ),
    "05_monolith_to_services": lambda: plan(monolith()),
    "06_blue_green": lambda: plan(routed(api=changed("api", replicas=4)), "blue_green", routed()),
    "07_potential_downtime": lambda: plan(
        shop(db=changed("db", encryption_at_rest=True)), constraints=ALLOWED
    ),
    "12_several_strategies": lambda: plan(
        routed(api=changed("api", replicas=4, health_check=True)),
        source_ir=routed(api=changed("api", health_check=True)),
    ),
    "13_manual_verification": lambda: plan(mysql(), constraints=ALLOWED, data_requirements=KEEP),
}


def test_fixture_01_vertical_scaling_states_its_explicit_configurations() -> None:
    proposal = FIXTURES["01_vertical_scaling"]()
    assert keys(proposal) == {"configure:api", "verify:api"}
    configure = next(s for s in proposal.steps if s.key == "configure:api")
    assert configure.completion == ("configuration.cpu_limit_cores is 2.",)  # the target's own value
    assert proposal.status is PlanStatus.DRAFT
    assert proposal.data_migrations == ()


def test_fixture_02_database_replacement_states_its_compatibility_requirements() -> None:
    proposal = FIXTURES["02_database_replacement"]()
    assert "prepare:db:freeze" in keys(proposal)  # offline: no replication is declared
    assert {c.key for c in proposal.compatibility} == {
        "data_model:db", "schema:db", "version:db", "clients:db", "authentication:db", "rollback:db",
    }  # fmt: skip
    assert {c.status for c in proposal.compatibility} == {CompatibilityStatus.UNKNOWN}  # never assumed
    [data] = proposal.data_migrations
    assert (data.source_element_id, data.destination_element_id, data.scope) == (
        "db",
        "db2",
        KEEP[0].statement,
    )
    assert proposal.status is PlanStatus.DRAFT


def test_fixture_03_missing_data_volume_is_unevaluable_never_invented() -> None:
    [data] = FIXTURES["03_missing_data_volume"]().data_migrations
    assert any("volume" in m for m in data.missing)
    assert data.to_dict()["duration"] == "unevaluable"


def test_fixture_04_replication_follows_its_declared_prerequisites() -> None:
    proposal = FIXTURES["04_replication"]()
    assert proposal.strategy == "replication_cutover"
    assert {"replicate:db", "backfill:db", "verify:db:consistency", "cutover:db"} <= keys(proposal)
    lag = next(c for c in proposal.checkpoints if c.key == "replication_lag:db")
    assert lag.status is CheckpointStatus.CANNOT_EVALUATE  # no threshold is stated
    option = next(o for o in proposal.alternatives if o.pattern == "replication_cutover@1")
    assert (option.supported, option.missing) == (True, ())


def test_fixture_05_monolith_to_services_keeps_ambiguous_boundaries_as_findings() -> None:
    proposal = FIXTURES["05_monolith_to_services"]()
    assert {FindingType.UNSUPPORTED_CHANGE, FindingType.MANUAL_INTERPRETATION} <= finding_types(proposal)
    touched = {e for s in proposal.steps for e in s.element_ids}
    assert not {"api", "domain"} & touched  # nothing invented for what cannot be planned
    assert {"provision:orders", "provision:payments"} <= keys(proposal)  # what can be planned, is
    assert proposal.status is PlanStatus.NEEDS_INFORMATION


def test_fixture_06_blue_green_needs_its_modeled_capabilities() -> None:
    proposal = FIXTURES["06_blue_green"]()
    assert proposal.strategy == "blue_green"
    assert {"provision:api:green", "cutover:api", "decommission:api:blue"} <= keys(proposal)
    unrouted = plan(shop(api=changed("api", replicas=4)), "blue_green")
    assert unrouted.strategy == "in_place"
    assert FindingType.STRATEGY_NOT_SUPPORTED in finding_types(unrouted)


def test_fixture_07_potential_downtime_is_stated_with_its_conditions() -> None:
    proposal = FIXTURES["07_potential_downtime"]()
    configure = next(s for s in proposal.steps if s.key == "configure:db")
    assert configure.downtime is DowntimeStatus.POTENTIAL_DOWNTIME
    assert configure.downtime_note
    downtime = next(r for r in proposal.risks if r.key == "downtime:configure:db")
    assert (downtime.status, downtime.preconditions) == (RiskStatus.POTENTIAL, (configure.downtime_note,))


def test_fixture_08_unknown_rollback_behaviour_is_a_stated_limitation() -> None:
    unknown = step("reroute:legacy", StepType.CONFIGURE, reversibility=Reversibility.UNKNOWN)
    [consideration] = rollbacks((unknown,), ())
    assert (consideration.reversibility, consideration.action) == (Reversibility.UNKNOWN, None)
    assert consideration.limitations  # never a generic undo
    [risk] = risks(context(shop(api=changed("api", replicas=4))), (unknown,), (), (), ())
    assert (risk.category, risk.status) == (RiskCategory.ROLLBACK_LIMITATION, RiskStatus.UNKNOWN)


def test_fixture_09_a_dependency_cycle_is_reported_and_blocks_review() -> None:
    cyclic = (step("a", StepType.CONFIGURE, "b"), step("b", StepType.CONFIGURE, "a"))
    result = sequence(cyclic, ANALYSIS)
    assert [f.type for f in result.findings] == [FindingType.DEPENDENCY_CYCLE]
    assert result.sequence == ()
    blocked = MigrationProposal(ANALYSIS.source, ANALYSIS.target, steps=cyclic, findings=result.findings)
    assert blocked.status is PlanStatus.NEEDS_INFORMATION


def test_fixture_10_invalid_step_references_are_reported() -> None:
    [finding] = sequence((step("a", StepType.CONFIGURE, "ghost"),), ANALYSIS).findings
    assert (finding.type, finding.key) == (FindingType.INVALID_DEPENDENCY, "a")


def test_fixture_11_a_changed_revision_makes_the_plan_stale() -> None:
    proposal = FIXTURES["01_vertical_scaling"]()
    now = CurrentState(proposal.source.content_hash, proposal.target.content_hash, 2, dict(proposal.models))
    assert not freshness(proposal, now).stale
    moved = dataclasses.replace(now, latest_revision=3)
    rewritten = dataclasses.replace(now, target_hash="0" * 64)
    assert freshness(proposal, moved).reasons == (StaleReason.NEWER_REVISION,)
    assert freshness(proposal, rewritten).reasons == (StaleReason.TARGET_CHANGED,)


def test_fixture_12_several_supported_strategies_are_shown_side_by_side() -> None:
    proposal = FIXTURES["12_several_strategies"]()
    supported = {o.pattern: o for o in proposal.alternatives if o.supported}
    assert {"in_place@1", "rolling@1", "blue_green@1"} <= set(supported)
    assert proposal.strategy == "in_place"  # the documented default: none is chosen by a score
    assert len({o.tradeoffs for o in supported.values()}) == len(supported)  # each with its own trade-offs
    assert not FABRICATED.search(json.dumps([o.to_dict() for o in proposal.alternatives]))


def test_fixture_13_a_plan_requiring_manual_verification_says_so() -> None:
    proposal = FIXTURES["13_manual_verification"]()
    manual = {s.key for s in proposal.steps if s.manual_verification}
    assert {"prepare:db:freeze", "cutover:db", "decommission:db:source"} <= manual
    awaiting = [c for c in proposal.checkpoints if c.status is CheckpointStatus.MANUAL_VERIFICATION_REQUIRED]
    assert {s.id for s in proposal.steps if s.manual_verification} <= {
        i for c in awaiting for i in c.step_ids
    }


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_14_no_plan_mutates_the_source_or_target(name: str) -> None:
    before = json.dumps(to_dict(SOURCE_IR))
    FIXTURES[name]()
    assert json.dumps(to_dict(SOURCE_IR)) == before
    after = mysql(replication_mode="asynchronous")
    snapshot = json.dumps(to_dict(after))
    plan(after, "replication_cutover")
    assert json.dumps(to_dict(after)) == snapshot


def test_fixture_15_a_rejected_then_revised_plan_keeps_its_prior_version() -> None:
    first = version()
    under_review = submit(first, current=Freshness(), user_id=ADA, at=AT)
    rejected = reject(
        under_review, version_number=1, fingerprint=under_review.proposal.fingerprint,
        comment="State the rollback for the cache.", user_id=BOB, at=AT,
    )  # fmt: skip
    revised = regenerate(
        rejected, version_id=uuid.UUID(int=99), request=rejected.request,
        proposal=FIXTURES["07_potential_downtime"](), user_id=ADA, at=AT,
    )  # fmt: skip
    assert revised.created is not None
    assert revised.previous.proposal is first.proposal  # the prior version's content is kept
    assert revised.previous.reviews[1].comment == "State the rollback for the cache."
    assert (revised.previous.status, revised.created.version) == (PlanStatus.SUPERSEDED, 2)


# --- across every fixture --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_fixture_is_deterministic(name: str) -> None:
    first, again = FIXTURES[name](), FIXTURES[name]()
    assert first.to_dict() == again.to_dict()  # structure, step ids, order, dependencies, risks, evidence
    assert [s.id for s in first.steps] == [s.id for s in again.steps]
    assert [stage.step_ids for stage in first.sequence] == [stage.step_ids for stage in again.sequence]
    assert first.fingerprint == again.fingerprint


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_fixture_survives_storage(name: str) -> None:
    proposal = FIXTURES[name]()
    stored = json.loads(json.dumps(proposal.to_dict()))
    assert proposal_from_dict(stored, proposal.fingerprint).to_dict() == proposal.to_dict()


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_no_fixture_fabricates_estimates_or_executes(name: str) -> None:
    proposal = FIXTURES[name]()
    text = json.dumps(proposal.to_dict())
    assert not FABRICATED.search(text.replace(KEEP[0].statement, ""))
    assert not re.search(r'"(executed|executed_at|started_at|completed_at)"', text)
    assert proposal.status in {PlanStatus.DRAFT, PlanStatus.NEEDS_INFORMATION}  # never approved by the engine
    for each in proposal.steps:
        assert {t.kind for t in each.traces} >= {TraceKind.CHANGE, TraceKind.PATTERN}


def test_a_transition_too_large_for_one_plan_is_refused_not_truncated() -> None:
    caches = tuple(node(f"cache{i:03}", NodeKind.CACHE) for i in range(200))  # three steps each
    with pytest.raises(InvalidMigrationRequest) as error:
        plan(ArchitectureIR("Shop", nodes=(*SOURCE_IR.nodes, *caches), connections=SOURCE_IR.connections))
    assert error.value.details == {"field": "target", "reason": "too_many_changes", "limit": 500}
