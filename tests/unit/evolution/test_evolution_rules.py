"""Evolution rules and candidate generation (Milestone 13, phase 2): versioned rules, each candidate
recording the rule that made it, preconditions and required evidence enforced, nothing fabricated
where inputs are missing, structural changes never proposed, constraints reported, deterministic,
and the baseline never modified."""

import json
import random
import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.capacity.units import Quantity
from core.domain.engine_results import Evidence
from core.domain.evolution.candidates import BaselineRef, Candidate, EvidenceRef, RuleRef
from core.domain.evolution.entities import EvolutionConstraints
from core.domain.evolution.errors import InvalidEvolutionResult
from core.domain.evolution.goals import EvolutionGoal, FindingRef
from core.domain.evolution.results import FindingType
from core.domain.evolution.triggers import Trigger, TriggerKind
from core.domain.evolution.values import Basis, CandidateCategory, EvidenceSource, EvidenceState, GoalType
from core.domain.observability.values import Dimension as Coverage
from core.domain.simulations.scenarios import ConfigurationChange
from engines.evolution.rulebook import RULES, default_registry
from engines.evolution.rules import DuplicateRule, Registry, RuleContext, RuleMeta, generate
from tests.unit.architecture_ir.builders import connection, node

S, G, K = EvidenceSource, GoalType, TriggerKind
ANALYSIS = str(uuid.UUID(int=8))


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: ConfigValue) -> Any:
    return node(node_id, kind, configuration=Configuration(values))


def shop() -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            component("api", replicas=2, cpu_limit_cores=Decimal(1), logs=False, traces=True),
            component("db", NodeKind.DATABASE, replicas=1, encryption_at_rest=False),
            component("cache", NodeKind.CACHE),
        ),
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https"),
            connection(
                "api-db",
                "api",
                "db",
                kind=ConnectionKind.DATA_ACCESS,
                protocol="postgresql",
                configuration=Configuration({"tls": False}),
            ),
        ),
    )


IR = shop()
BASELINE = BaselineRef(uuid.UUID(int=7), 1, content_hash(IR))
WORKLOAD = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(200, "requests/second"))


def finding_goal(source: EvidenceSource, finding_id: str) -> EvolutionGoal:
    return EvolutionGoal(G.ADDRESS_FINDING, finding=FindingRef(source, finding_id))


def evidence(source: EvidenceSource, item: str) -> EvidenceRef:
    return EvidenceRef(source, ANALYSIS, EvidenceState.CURRENT, item, 1, BASELINE.content_hash, "m1")


def scaling(element: str, code: str, scaling: str, current: str, required: str, goal: str) -> Trigger:
    facts = (
        Evidence("scaling", scaling),
        Evidence("current", current),
        Evidence("required", required),
        Evidence("model", "replica-throughput" if code == "work_rate" else "cpu-demand"),
        Evidence("basis", "as stated by the model"),
    )
    return Trigger(
        K.SCALING_OPTION, S.CAPACITY, code, goal, element, evidence(S.CAPACITY, f"{element}:{code}"), facts
    )


def found(source: EvidenceSource, code: str, element: str, goal: str, message: str | None = None) -> Trigger:
    return Trigger(
        K.FINDING, source, code, goal, element, evidence(source, f"{source.value}_{element}"), (), message
    )


def run(
    triggers: list[Trigger], goals: tuple[EvolutionGoal, ...], ir: ArchitectureIR = IR, **constraints: Any
) -> Any:
    context = RuleContext(ir, BASELINE, {g.key: g for g in goals}, EvolutionConstraints(**constraints))
    return generate(context, triggers, default_registry())


def test_every_rule_is_versioned_and_declares_its_contract() -> None:
    registry = default_registry()
    ids = [r.meta.id for r in registry.rules()]
    assert ids == sorted(ids) == [
        "add-replica", "enable-signal", "encrypt-at-rest", "require-tls", "scale-cpu", "scale-replicas",
    ]  # fmt: skip
    for rule in RULES:
        meta = rule.meta.to_dict()
        for field in ("triggers", "goals", "evidence", "properties", "preconditions", "benefits", "tradeoffs",
                      "unsupported", "validation", "analyses"):  # fmt: skip
            assert meta[field], (rule.meta.id, field)
        assert rule.meta.version == 1
    assert ("scale-replicas", 1) in registry.model_set().models
    with pytest.raises(DuplicateRule):
        Registry((*RULES, RULES[0]))


def test_a_rule_may_only_change_properties_the_ir_defines() -> None:
    with pytest.raises(ValueError, match="does not define"):
        RuleMeta(
            "add-cache", 1, "Add a cache", "x", CandidateCategory.SCALING, ((S.CAPACITY, "work_rate"),),
            (G.INCREASE_WORKLOAD,), (S.CAPACITY,), ("cache_layer",), (), (), (), (), (), (), (),
        )  # fmt: skip


def test_fixture_1_a_capacity_scaling_option_becomes_a_resource_candidate() -> None:
    generation = run([scaling("api", "work_rate", "horizontal", "2", "4", WORKLOAD.key)], (WORKLOAD,))
    [candidate] = generation.candidates
    assert (candidate.rule, candidate.category) == (RuleRef("scale-replicas", 1), CandidateCategory.SCALING)
    assert candidate.changes == (ConfigurationChange("api", "replicas", 4),)
    assert candidate.goals == (WORKLOAD.key,)
    assert candidate.evidence[0].item == "api:work_rate"
    [benefit] = candidate.benefits
    assert benefit.basis is Basis.MODELED
    assert Evidence("required", "4") in benefit.evidence  # the model's own statement
    assert {e.basis for e in (*candidate.complexity, *candidate.migration, *candidate.risks)} == {
        Basis.CONSIDERATION
    }
    assert generation.findings == ()


def test_a_vertical_option_proposes_cpu_and_both_are_alternatives() -> None:
    triggers = [
        scaling("api", "cpu", "horizontal", "2", "3", WORKLOAD.key),
        scaling("api", "cpu", "vertical", "1", "1.5", WORKLOAD.key),
    ]
    generation = run(triggers, (WORKLOAD,))
    changes = sorted(c.changes[0].to_dict()["property"] for c in generation.candidates)
    assert changes == ["cpu_limit_cores", "replicas"]  # two alternatives, neither chosen


def test_fixture_2_evidence_that_does_not_describe_the_architecture_proposes_nothing() -> None:
    generation = run([scaling("cache", "work_rate", "horizontal", "1", "3", WORKLOAD.key)], (WORKLOAD,))
    assert generation.candidates == ()
    [finding] = generation.findings
    assert finding.type is FindingType.NO_APPLICABLE_RULE
    assert finding.missing == ("cache.configuration.replicas",)  # cache declares no replicas
    mismatch = run([scaling("api", "work_rate", "horizontal", "3", "5", WORKLOAD.key)], (WORKLOAD,))
    assert mismatch.candidates == ()
    assert "does not describe it" in mismatch.findings[0].message


def test_fixture_4_a_single_point_of_failure_gets_a_second_replica_with_its_unknowns() -> None:
    goal = finding_goal(S.RELIABILITY, "rel_db")
    [candidate] = run([found(S.RELIABILITY, "single_point_of_failure", "db", goal.key)], (goal,)).candidates
    assert (candidate.category, candidate.changes) == (
        CandidateCategory.REDUNDANCY,
        (ConfigurationChange("db", "replicas", 2),),
    )
    assert candidate.missing == ("db.configuration.failover_mode", "db.configuration.failure_independence")
    already = run([found(S.RELIABILITY, "single_point_of_failure", "api", goal.key)], (goal,))
    assert already.candidates == ()
    assert "already declares 2 replicas" in already.findings[0].message


def test_fixture_5_a_security_finding_linked_to_a_supported_control() -> None:
    goal = finding_goal(S.SECURITY, "sec_1")
    triggers = [
        found(S.SECURITY, "unencrypted_data_in_transit", "api-db", goal.key),
        found(S.SECURITY, "unencrypted_data_at_rest", "db", goal.key),
    ]
    candidates = run(triggers, (goal,)).candidates
    assert sorted((c.rule.id, c.changes[0].element_id) for c in candidates) == [
        ("encrypt-at-rest", "db"),
        ("require-tls", "api-db"),
    ]
    assert {c.category for c in candidates} == {CandidateCategory.SECURITY_CONTROL}


def test_fixture_6_an_observability_gap_proposes_the_absent_signal() -> None:
    goal = EvolutionGoal(G.OBSERVABILITY_COVERAGE, dimension=Coverage.LOGGING)
    [candidate] = run([found(S.OBSERVABILITY, "logs_absent", "api", goal.key)], (goal,)).candidates
    assert candidate.changes == (ConfigurationChange("api", "logs", True),)
    assert candidate.category is CandidateCategory.INSTRUMENTATION


def test_no_mechanism_or_kind_is_invented() -> None:
    goal = finding_goal(S.SECURITY, "sec_2")
    metrics = finding_goal(S.OBSERVABILITY, "obs_2")
    triggers = [
        found(
            S.SECURITY, "missing_authentication", "api", goal.key, "Review whether api should authenticate."
        ),
        found(S.OBSERVABILITY, "metrics_absent", "api", metrics.key),
    ]
    generation = run(triggers, (goal, metrics))
    assert generation.candidates == ()
    assert {f.type for f in generation.findings} == {FindingType.NO_APPLICABLE_RULE}
    assert any("Review whether api should authenticate." in f.message for f in generation.findings)


def test_no_structural_change_is_ever_proposed() -> None:
    trigger = Trigger(
        K.SCALING_UNSUPPORTED, S.CAPACITY, "work_rate", WORKLOAD.key, "db", evidence(S.CAPACITY, "db")
    )
    generation = run([trigger], (WORKLOAD,))
    assert generation.candidates == ()
    [finding] = generation.findings
    assert finding.type is FindingType.STRUCTURAL_CONSIDERATION
    assert "none is proposed" in finding.message
    assert finding.missing == ("db: a model of how work_rate scales",)


def test_constraints_exclude_candidates_visibly() -> None:
    trigger = scaling("api", "work_rate", "horizontal", "2", "4", WORKLOAD.key)
    for constraints, words in (
        ({"frozen_elements": ("api",)}, "which the request freezes"),
        ({"excluded_categories": (CandidateCategory.SCALING,)}, "excludes scaling"),
        ({"max_replicas": 3}, "more than 3 replicas"),
    ):
        generation = run([trigger], (WORKLOAD,), **constraints)
        assert generation.candidates == ()
        [finding] = generation.findings
        assert finding.type is FindingType.EXCLUDED_BY_CONSTRAINT
        assert words in finding.message


def test_one_proposal_serving_two_goals_is_one_candidate() -> None:
    other = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(150, "requests/second"))
    triggers = [
        scaling("api", "work_rate", "horizontal", "2", "4", WORKLOAD.key),
        scaling("api", "work_rate", "horizontal", "2", "4", other.key),
    ]
    [candidate] = run(triggers, (WORKLOAD, other)).candidates
    assert candidate.goals == tuple(sorted((WORKLOAD.key, other.key)))


def test_a_rule_that_breaks_its_declaration_is_caught() -> None:
    class Rogue:
        meta = RULES[3].meta  # require-tls

        def propose(self, trigger: Trigger, context: RuleContext) -> tuple[Candidate, ...]:
            return (
                Candidate(
                    RuleRef("require-tls", 1), context.baseline, CandidateCategory.SECURITY_CONTROL, "Rogue",
                    "Scales instead.", (ConfigurationChange("api", "replicas", 9),), (trigger.goal,), "None.",
                    (trigger.evidence,),
                ),
            )  # fmt: skip

    goal = finding_goal(S.SECURITY, "sec_1")
    context = RuleContext(IR, BASELINE, {goal.key: goal})
    with pytest.raises(InvalidEvolutionResult, match="malformed"):
        generate(
            context,
            [found(S.SECURITY, "unencrypted_data_in_transit", "api-db", goal.key)],
            Registry([Rogue()]),
        )


def test_generation_is_deterministic_and_never_modifies_the_baseline() -> None:
    goals = (WORKLOAD, finding_goal(S.SECURITY, "sec_1"), finding_goal(S.RELIABILITY, "rel_db"))
    triggers = [
        scaling("api", "work_rate", "horizontal", "2", "4", goals[0].key),
        found(S.SECURITY, "unencrypted_data_in_transit", "api-db", goals[1].key),
        found(S.SECURITY, "missing_authentication", "api", goals[1].key),
        found(S.RELIABILITY, "single_point_of_failure", "db", goals[2].key),
    ]
    before = json.dumps(to_dict(IR), sort_keys=True)
    first = run(triggers, goals)
    for seed in (1, 2, 3):
        shuffled = list(triggers)
        random.Random(seed).shuffle(shuffled)  # noqa: S311 -- a reproducible order, not a secret
        again = run(shuffled, goals)
        assert [c.to_dict() for c in again.candidates] == [c.to_dict() for c in first.candidates]
        assert [f.to_dict() for f in again.findings] == [f.to_dict() for f in first.findings]
    assert json.dumps(to_dict(IR), sort_keys=True) == before
    assert content_hash(IR) == BASELINE.content_hash
