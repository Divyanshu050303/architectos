"""Dependency graph and transition validation (Migration Planning, phase 5): references checked, cycles
detected, a deterministic order, parallel stages only when explicit, missing prerequisites and
missing manual verification reported as findings — never repaired silently."""

import dataclasses
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.domain.migrations.changes import ChangeAnalysis
from core.domain.migrations.errors import InvalidMigrationPlan
from core.domain.migrations.plans import MigrationProposal, Stage
from core.domain.migrations.steps import MigrationStep, Trace, step_id
from core.domain.migrations.values import (
    DowntimeStatus,
    FindingType,
    PlanStatus,
    Reversibility,
    StepType,
    TraceKind,
)
from engines.migration.dependency_graph import DependencyGraph
from engines.migration.sequencing import sequence
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.migration.test_migration_changes import SOURCE, SOURCE_IR, changed, original, run, shop
from tests.unit.migration.test_migration_patterns import mysql
from tests.unit.migration.test_migration_steps import plan, routed

ANALYSIS = run(shop(api=changed("api", replicas=4)))


def step(key: str, type_: StepType = StepType.CONFIGURE, *after: str, **fields: Any) -> MigrationStep:
    return MigrationStep(
        key,
        type_,
        key,
        f"{key} is done.",
        (Trace(TraceKind.CHANGE, "node:api:modified"),),
        ("It is done.",),
        depends_on=tuple(step_id(a) for a in after),
        **fields,
    )


def found(steps: tuple[MigrationStep, ...], analysis: ChangeAnalysis = ANALYSIS) -> dict[str, FindingType]:
    return {f.key: f.type for f in sequence(steps, analysis).findings}


def with_cache() -> ArchitectureIR:
    return dataclasses.replace(
        shop(cache=node("cache", NodeKind.CACHE)),
        connections=(*SOURCE_IR.connections, connection("api-cache", "api", "cache", protocol="redis")),
    )


def without_db() -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(original("web"), original("api")),
        connections=tuple(c for c in SOURCE_IR.connections if c.id == "web-api"),
    )


def replaced() -> ArchitectureIR:
    db2 = node("db2", NodeKind.DATABASE, technology=Technology("mysql", "8"))
    return ArchitectureIR(
        "Shop",
        nodes=(*(n for n in SOURCE_IR.nodes if n.id != "db"), db2),
        connections=(
            *(c for c in SOURCE_IR.connections if c.id == "web-api"),
            connection("api-db2", "api", "db2", kind=ConnectionKind.DATA_ACCESS, protocol="mysql"),
        ),
    )


SCENARIOS = {
    "in_place": lambda: plan(shop(api=changed("api", replicas=4))),
    "rolling": lambda: plan(shop(api=changed("api", replicas=4, health_check=True)), strategy="rolling"),
    "blue_green": lambda: plan(routed(api=changed("api", replicas=4)), "blue_green", routed()),
    "added": lambda: plan(with_cache()),
    "removed": lambda: plan(without_db()),
    "offline": lambda: plan(mysql()),
    "replication": lambda: plan(mysql(replication_mode="asynchronous"), strategy="replication_cutover"),
    "replacement": lambda: plan(replaced()),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_generated_plan_is_sequenced_with_its_prerequisites(name: str) -> None:
    proposal: MigrationProposal = SCENARIOS[name]()
    assert not [f for f in proposal.findings if f.type is FindingType.MISSING_PREREQUISITE]
    stage_of = {i: s.number for s in proposal.sequence for i in s.step_ids}
    assert sorted(stage_of) == sorted(s.id for s in proposal.steps)  # every step exactly once
    for each in proposal.steps:
        assert all(stage_of[d] < stage_of[each.id] for d in each.depends_on)  # after what it waits for
    assert not any(s.parallel for s in proposal.sequence)  # no rule marks a step parallelizable
    assert proposal.summary()["stages"] == len(proposal.steps)
    assert proposal.status is PlanStatus.DRAFT


def test_the_order_is_deterministic_with_ties_broken_by_key() -> None:
    steps = (step("c"), step("a"), step("b", StepType.CONFIGURE, "c"))
    for ordering in (steps, tuple(reversed(steps))):
        stages = sequence(ordering, ANALYSIS).sequence
        assert [s.step_ids for s in stages] == [(step_id("a"),), (step_id("c"),), (step_id("b"),)]
        assert [s.number for s in stages] == [1, 2, 3]


def test_a_dependency_on_a_missing_step_is_reported_and_nothing_is_ordered() -> None:
    result = sequence((step("a"), step("b", StepType.CONFIGURE, "ghost")), ANALYSIS)
    [finding] = result.findings
    assert (finding.type, finding.key, finding.step_ids) == (
        FindingType.INVALID_DEPENDENCY,
        "b",
        (step_id("b"),),
    )
    assert finding.missing == (f"The step {step_id('ghost')}, or no dependency on it.",)
    assert result.sequence == ()


def test_a_cycle_is_reported_with_every_step_in_it() -> None:
    steps = (
        step("a", StepType.CONFIGURE, "c"),
        step("b", StepType.CONFIGURE, "a"),
        step("c", StepType.CONFIGURE, "b"),
        step("d", StepType.CONFIGURE, "a"),  # waits for the cycle; is not in it
        step("e", StepType.CONFIGURE, "f"),
        step("f", StepType.CONFIGURE, "e"),
    )
    result = sequence(steps, ANALYSIS)
    assert [(f.type, f.key, set(f.step_ids)) for f in result.findings] == [
        (FindingType.DEPENDENCY_CYCLE, "a", {step_id(k) for k in "abc"}),
        (FindingType.DEPENDENCY_CYCLE, "e", {step_id(k) for k in "ef"}),
    ]
    assert result.sequence == ()
    assert len(DependencyGraph.of(steps).cycles()) == 2


def test_a_cycle_keeps_the_plan_from_review() -> None:
    steps = (step("a", StepType.CONFIGURE, "b"), step("b", StepType.CONFIGURE, "a"))
    result = sequence(steps, ANALYSIS)
    proposal = MigrationProposal(SOURCE, ANALYSIS.target, steps=steps, findings=result.findings)
    assert (proposal.status, proposal.sequence) == (PlanStatus.NEEDS_INFORMATION, ())


def test_a_long_chain_is_ordered_without_recursion() -> None:
    chain = tuple(step(f"s{n:03}", StepType.CONFIGURE, *((f"s{n - 1:03}",) if n else ())) for n in range(450))
    result = sequence(chain, ANALYSIS)
    assert result.findings == ()
    assert [s.step_ids[0] for s in result.sequence] == [s.id for s in chain]


def test_steps_are_grouped_only_when_each_is_explicitly_parallelizable() -> None:
    steps = (
        step("a", parallelizable=True),
        step("b", parallelizable=True),
        step("c"),
        step("d", StepType.VERIFY, "a", "b", "c"),
    )
    stages = sequence(steps, ANALYSIS).sequence
    assert [(s.number, s.parallel, s.step_ids) for s in stages] == [
        (1, True, (step_id("a"), step_id("b"))),
        (2, False, (step_id("c"),)),
        (3, False, (step_id("d"),)),
    ]
    alone = sequence((step("a", parallelizable=True), step("c")), ANALYSIS).sequence
    assert not any(s.parallel for s in alone)  # one parallelizable step is not a group


def test_a_sequence_must_cover_each_step_once_and_respect_explicit_parallelism() -> None:
    a, c = step("a", parallelizable=True), step("c")
    base: dict[str, Any] = {"source": SOURCE, "target": ANALYSIS.target, "steps": (a, c)}
    MigrationProposal(**base, sequence=(Stage(1, (a.id,)), Stage(2, (c.id,))))
    for wrong in (
        (Stage(1, (a.id,)),),  # c is missing
        (Stage(1, (a.id,)), Stage(3, (c.id,))),  # not numbered from 1 in order
        (Stage(1, (a.id, c.id), parallel=True),),  # c is not parallelizable
    ):
        with pytest.raises(InvalidMigrationPlan):
            MigrationProposal(**base, sequence=wrong)
    with pytest.raises(InvalidMigrationPlan):
        Stage(1, (a.id,), parallel=True)  # a group of one
    with pytest.raises(InvalidMigrationPlan):
        Stage(1, (a.id, c.id))  # several steps together only when parallel


def test_a_step_on_an_added_element_needs_its_provisioning_first() -> None:
    analysis = run(with_cache())
    unprovisioned = (step("configure:cache", element_ids=("cache",)),)
    assert found(unprovisioned, analysis) == {
        "provisioned:cache:configure:cache": FindingType.MISSING_PREREQUISITE
    }
    provisioned = (
        step("provision:cache", StepType.PROVISION, element_ids=("cache",)),
        step("configure:cache", StepType.CONFIGURE, "provision:cache", element_ids=("cache",)),
    )
    assert found(provisioned, analysis) == {}
    early = (step("provision:api-cache", StepType.PROVISION, element_ids=("api", "api-cache", "cache")),)
    assert found(early, analysis) == {  # a connection needs its new endpoint, not only itself
        "provisioned:cache:provision:api-cache": FindingType.MISSING_PREREQUISITE
    }


def test_an_element_is_retired_only_after_every_step_concerning_it() -> None:
    steps = (
        step("verify:db:unused", StepType.VERIFY, element_ids=("db",)),
        step("backfill:db", StepType.BACKFILL, element_ids=("db",)),
        step("decommission:db", StepType.DECOMMISSION, "verify:db:unused", element_ids=("db",),
             reversibility=Reversibility.IRREVERSIBLE, manual_verification=True),
    )  # fmt: skip
    assert found(steps, run(without_db())) == {"retired:db:decommission:db": FindingType.MISSING_PREREQUISITE}


def test_cutovers_and_decommissioning_need_a_verification_before_them() -> None:
    steps = (
        step("cutover:db", StepType.CUTOVER, manual_verification=True),
        step("decommission:x", StepType.DECOMMISSION),
    )
    assert found(steps) == {
        "verified:cutover:db": FindingType.MISSING_PREREQUISITE,
        "verified:decommission:x": FindingType.MISSING_PREREQUISITE,
    }
    prepared = (step("prepare:db", StepType.PREPARE), step("cutover:db", StepType.CUTOVER, "prepare:db",
                                                           manual_verification=True))  # fmt: skip
    assert found(prepared) == {}


@pytest.mark.parametrize(
    "fields",
    [
        {"type_": StepType.CUTOVER},
        {"reversibility": Reversibility.IRREVERSIBLE},
        {"downtime": DowntimeStatus.KNOWN_DOWNTIME},
        {"downtime": DowntimeStatus.POTENTIAL_DOWNTIME, "downtime_note": "Writes pause."},
    ],
)
def test_consequential_steps_need_manual_verification(fields: dict[str, Any]) -> None:
    type_ = fields.pop("type_", StepType.PREPARE)
    base = step("verify:x", StepType.VERIFY)
    unverified = (base, step("move", type_, "verify:x", **fields))
    assert found(unverified) == {"manual:move": FindingType.MISSING_PREREQUISITE}
    verified = (base, step("move", type_, "verify:x", manual_verification=True, **fields))
    assert found(verified) == {}
