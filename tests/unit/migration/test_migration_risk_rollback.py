"""Risk, verification and rollback (Migration Planning, phase 7): risks from the plan's own evidence
with a status and no score; checkpoints with clear status semantics, none passed at planning time;
rollback considerations examined per step, stating divergence and limitations rather than a
generic undo."""

import dataclasses
import json
import re

import pytest

from core.domain.evolution.candidates import EvidenceRef
from core.domain.evolution.values import EvidenceSource, EvidenceState
from core.domain.migrations.errors import InvalidMigrationPlan
from core.domain.migrations.plans import MigrationProposal, PlanFinding
from core.domain.migrations.steps import Checkpoint, Risk, RollbackConsideration, Trace
from core.domain.migrations.values import (
    CheckpointBasis,
    CheckpointStatus,
    FindingType,
    Reversibility,
    RiskCategory,
    RiskStatus,
    TraceKind,
)
from engines.migration.risk import risks
from tests.unit.migration.test_migration_changes import SOURCE, changed, shop
from tests.unit.migration.test_migration_data_downtime import ALLOWED, KEEP, plan
from tests.unit.migration.test_migration_patterns import context, mysql
from tests.unit.migration.test_migration_sequencing import replaced, without_db
from tests.unit.migration.test_migration_steps import routed

C, S = RiskCategory, RiskStatus
SCENARIOS = {
    "offline": lambda: plan(mysql(), constraints=ALLOWED, data_requirements=KEEP),
    "replication": lambda: plan(mysql(replication_mode="asynchronous"), "replication_cutover"),
    "replacement": lambda: plan(replaced(), constraints=ALLOWED, data_requirements=KEEP),
    "removed": lambda: plan(without_db()),
    "rolling": lambda: plan(shop(api=changed("api", replicas=4, health_check=True)), "rolling"),
    "blue_green": lambda: plan(routed(api=changed("api", replicas=4)), "blue_green", routed()),
    "in_place": lambda: plan(shop(db=changed("db", encryption_at_rest=True)), constraints=ALLOWED),
}


def risk_of(proposal: MigrationProposal) -> dict[str, Risk]:
    return {r.key: r for r in proposal.risks}


def checks_of(proposal: MigrationProposal) -> dict[str, Checkpoint]:
    return {c.key: c for c in proposal.checkpoints}


def rollback_of(proposal: MigrationProposal) -> dict[str, RollbackConsideration]:
    by_step = {s.id: s.key for s in proposal.steps}
    return {by_step[r.step_id]: r for r in proposal.rollbacks}


# --- risks -----------------------------------------------------------------------------------------


def test_a_data_move_names_its_loss_divergence_downtime_and_irreversible_risks() -> None:
    proposal = SCENARIOS["offline"]()
    found = risk_of(proposal)
    assert (found["data_loss:db"].category, found["data_loss:db"].status) == (C.DATA_LOSS, S.POTENTIAL)
    assert found["data_loss:db"].preconditions == (
        "Writes reach db's source instance after the copy to db's target instance begins.",
    )
    divergence = found["divergence:db"]
    assert (divergence.category, divergence.status) == (C.DATA_INCONSISTENCY, S.POTENTIAL)
    assert {s.key for s in proposal.steps if s.id in divergence.step_ids} == {"cutover:db"}
    assert found["downtime:prepare:db:freeze"].status is S.CONFIRMED  # the method requires it
    assert found["irreversible:decommission:db:source"].category is C.IRREVERSIBLE_CHANGE
    assert found["compatibility:db"].status is S.UNKNOWN  # unanswered, never assumed fine


def test_a_removal_without_stated_retention_is_a_confirmed_data_loss() -> None:
    assert risk_of(plan(without_db()))["data_loss:db"].status is S.CONFIRMED
    stated = risk_of(plan(without_db(), data_requirements=KEEP))["data_loss:db"]
    assert stated.status is S.POTENTIAL
    assert stated.preconditions == ("The stated retention of db's data is not carried out first.",)


def test_strategies_carry_their_capacity_and_cost_risks() -> None:
    assert risk_of(SCENARIOS["rolling"]())["capacity:configure:api"].category is C.CAPACITY_EXHAUSTION
    assert risk_of(SCENARIOS["blue_green"]())["cost:provision:api:green"].category is C.COST_INCREASE
    assert risk_of(SCENARIOS["replication"]())["capacity:replicate:db"].status is S.POTENTIAL


def test_security_and_observability_settings_switched_off_are_confirmed_regressions() -> None:
    source = shop(db=changed("db", encryption_at_rest=True))
    encryption = risk_of(plan(shop(), source_ir=source))["security:db:encryption_at_rest"]
    assert (encryption.category, encryption.status) == (C.SECURITY_REGRESSION, S.CONFIRMED)
    logs = risk_of(plan(shop(api=changed("api", logs=False))))["observability:api:logs"]
    assert (logs.category, logs.status) == (C.OBSERVABILITY_GAP, S.CONFIRMED)
    assert "security:db:encryption_at_rest" not in risk_of(SCENARIOS["in_place"]())  # switched on


def test_dependency_findings_are_ordering_risks() -> None:
    cycle = PlanFinding(FindingType.DEPENDENCY_CYCLE, "a", "a and b wait for each other.", missing=("Fix.",))
    [risk] = risks(context(shop(api=changed("api", replicas=4))), (), (), (), (cycle,))
    assert (risk.key, risk.category, risk.status) == (
        "ordering:dependency_cycle:a", C.DEPENDENCY_ORDERING, S.CONFIRMED,
    )  # fmt: skip
    assert risk.mitigation == "Fix."


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_risk_rests_on_evidence_and_has_no_score(name: str) -> None:
    proposal = SCENARIOS[name]()
    for risk in proposal.risks:
        assert risk.traces
        assert risk.mitigation
        assert risk.status is not S.POTENTIAL or risk.preconditions
    text = json.dumps([r.to_dict() for r in proposal.risks]).lower()
    assert not any(word in text for word in ('"score"', '"probability"', '"likelihood"', '"severity"'))


# --- verification checkpoints ----------------------------------------------------------------------


def test_engine_checkpoints_are_not_run_until_an_analysis_is_attached() -> None:
    found = checks_of(SCENARIOS["rolling"]())
    validation, capacity = found["validation:target"], found["capacity:target"]
    assert (validation.status, validation.basis, validation.blocking) == (
        CheckpointStatus.NOT_RUN, CheckpointBasis.MODELED, True,
    )  # fmt: skip
    assert (capacity.status, capacity.blocking) == (CheckpointStatus.NOT_RUN, False)
    assert "observability:target" in found  # health_check is an observability input


def test_data_checkpoints_are_for_a_person_or_the_running_system() -> None:
    found = checks_of(SCENARIOS["replication"]())
    consistency, lag = found["consistency:db"], found["replication_lag:db"]
    assert (consistency.status, consistency.basis) == (
        CheckpointStatus.MANUAL_VERIFICATION_REQUIRED, CheckpointBasis.MANUAL,
    )  # fmt: skip
    assert (lag.status, lag.basis) == (CheckpointStatus.CANNOT_EVALUATE, CheckpointBasis.RUNTIME_OBSERVED)
    assert "none is stated" in lag.expected  # no threshold invented
    assert "caught up" in found["cutover:cutover:db"].expected
    assert "rollback:cutover:db" in found  # its retained resources are confirmed before proceeding


def test_health_checks_are_observed_only_at_runtime() -> None:
    health = checks_of(SCENARIOS["rolling"]())["health:verify:api"]
    assert (health.status, health.basis) == (CheckpointStatus.NOT_RUN, CheckpointBasis.RUNTIME_OBSERVED)
    assert health.expected == "api's health check passes."


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_no_checkpoint_is_passed_at_planning_time(name: str) -> None:
    proposal = SCENARIOS[name]()
    evaluated = {CheckpointStatus.PASS, CheckpointStatus.FAIL, CheckpointStatus.WARNING}
    assert not [c for c in proposal.checkpoints if c.status in evaluated]
    manual = {s.id for s in proposal.steps if s.manual_verification}
    assert manual <= {i for c in proposal.checkpoints for i in c.step_ids}  # each is verified by a person


def test_a_pass_needs_current_modeled_evidence() -> None:
    trace = (Trace(TraceKind.PATTERN, "migration_planner@1"),)
    with pytest.raises(InvalidMigrationPlan):
        Checkpoint("x", "X", "Y", CheckpointStatus.PASS, CheckpointBasis.MODELED, trace)
    stale = EvidenceRef(EvidenceSource.CAPACITY, "a1", EvidenceState.STALE, None, 1, SOURCE.content_hash)
    with pytest.raises(InvalidMigrationPlan):
        Checkpoint("x", "X", "Y", CheckpointStatus.PASS, CheckpointBasis.MODELED, trace, evidence=(stale,))
    current = dataclasses.replace(stale, state=EvidenceState.CURRENT)
    with pytest.raises(InvalidMigrationPlan):  # never passed by a person or at runtime while planning
        Checkpoint("x", "X", "Y", CheckpointStatus.PASS, CheckpointBasis.MANUAL, trace, evidence=(current,))
    Checkpoint("x", "X", "Y", CheckpointStatus.PASS, CheckpointBasis.MODELED, trace, evidence=(current,))


# --- rollback --------------------------------------------------------------------------------------


def test_switching_back_after_a_data_cutover_states_the_divergence() -> None:
    cutover = rollback_of(SCENARIOS["offline"]())["cutover:db"]
    assert cutover.reversibility is Reversibility.CONDITIONALLY_REVERSIBLE
    assert cutover.consistency == (
        "Writes db's target instance accepts after the cutover are not in db's source instance: switching "
        "back makes the two diverge."
    )
    assert cutover.limitations
    assert any("reconciled" in p for p in cutover.preconditions)


def test_each_high_impact_step_is_considered_for_what_it_does() -> None:
    offline = rollback_of(SCENARIOS["offline"]())
    assert offline["prepare:db:freeze"].action == "Resume writes to db."
    source = offline["decommission:db:source"]
    assert (source.reversibility, source.action) == (Reversibility.IRREVERSIBLE, None)
    assert source.limitations == ("Once removed, db and its data cannot be restored by this plan.",)
    assert "verify:db:consistency" not in offline  # a verification changes nothing
    blue_green = rollback_of(SCENARIOS["blue_green"]())["cutover:api"]
    assert blue_green.retained == ("The previous environment of api, until it is retired.",)
    in_place = rollback_of(SCENARIOS["in_place"]())["configure:db"]
    assert (in_place.reversibility, in_place.action) == (
        Reversibility.CONDITIONALLY_REVERSIBLE, "Apply db's source configuration again.",
    )  # fmt: skip
    rolling = rollback_of(SCENARIOS["rolling"]())["configure:api"]
    assert rolling.action == "Roll api's source configuration back out one instance at a time."


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_rollback_considerations_are_proposals_in_words(name: str) -> None:
    text = json.dumps([r.to_dict() for r in SCENARIOS[name]().rollbacks]).lower()
    assert not re.search(r"\bundo\b", text)  # never a generic undo
    assert not any(word in text for word in ("kubectl", "terraform", "psql", "sudo", "$ "))


def test_risks_checkpoints_and_rollbacks_are_deterministic() -> None:
    first, again = SCENARIOS["replacement"](), SCENARIOS["replacement"]()
    assert [r.to_dict() for r in first.risks] == [r.to_dict() for r in again.risks]
    assert [c.to_dict() for c in first.checkpoints] == [c.to_dict() for c in again.checkpoints]
    assert [r.to_dict() for r in first.rollbacks] == [r.to_dict() for r in again.rollbacks]
    assert first.fingerprint == again.fingerprint
