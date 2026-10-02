"""Engine integration (Migration Planning, phase 9): stored analyses reused through their own
contracts, matched to the plan's exact source and target revisions; stale and missing evidence
explicit; checkpoints evaluated only from current analyses; comparisons only under the same
assumptions; nothing recomputed or invented."""

import uuid
from typing import Any

import pytest

from core.architecture_ir.serialization import content_hash
from core.domain.engine_results import Evidence
from core.domain.evolution.evidence import EvidenceItem, RequirementCheck, StoredAnalysis
from core.domain.evolution.triggers import TriggerKind
from core.domain.evolution.values import EvidenceSource, EvidenceState
from core.domain.migrations.entities import MigrationRequest, TargetSpec
from core.domain.migrations.evidence import AnalysisEvidence, CoverageState, Side
from core.domain.migrations.plans import MigrationProposal
from core.domain.migrations.steps import Checkpoint, Risk
from core.domain.migrations.values import (
    CheckpointBasis,
    CheckpointStatus,
    FindingType,
    PlanStatus,
    RiskStatus,
)
from core.domain.migrations.versioning import CurrentState, StaleReason, freshness
from engines.migration.changes import analyze, candidate_target
from engines.migration.evidence import integrate
from engines.migration.patternbook import default_registry
from engines.migration.patterns import PlanningContext
from engines.migration.planner import generate
from tests.unit.migration.test_migration_changes import (
    ARCHITECTURE,
    SCALE_API,
    SOURCE,
    SOURCE_IR,
    candidate,
    changed,
    shop,
)
from tests.unit.migration.test_migration_data_downtime import ALLOWED, plan

E, P, CS = EvidenceSource, CheckpointStatus, CoverageState
AFTER = shop(api=changed("api", replicas=4, logs=False), db=changed("db", encryption_at_rest=True))


def proposal() -> MigrationProposal:
    return plan(AFTER, constraints=ALLOWED)


def stored(
    source: EvidenceSource, side: str = "target", n: int = 1, status: str = "completed", **fields: Any
) -> StoredAnalysis:
    revision, digest = {
        "source": (1, SOURCE.content_hash),
        "target": (2, content_hash(AFTER)),
        "stale": (1, "e" * 64),
    }[side]
    return StoredAnalysis(source, uuid.UUID(int=n), revision, digest, "1", status, **fields)


def checkpoints(of: MigrationProposal) -> dict[str, Checkpoint]:
    return {c.key: c for c in of.checkpoints}


def risks(of: MigrationProposal) -> dict[str, Risk]:
    return {r.key: r for r in of.risks}


def finding(code: str, element: str, item: str) -> EvidenceItem:
    return EvidenceItem(TriggerKind.FINDING, code, element, item, message=f"Fix {code}.")


# --- matching and coverage -------------------------------------------------------------------------


def test_analyses_are_matched_to_the_exact_source_and_target() -> None:
    analyses = [
        AnalysisEvidence(stored(E.CAPACITY, "source", 1)),
        AnalysisEvidence(stored(E.CAPACITY, "target", 2)),
        AnalysisEvidence(stored(E.SECURITY, "stale", 3)),
    ]
    integrated = integrate(proposal(), analyses)
    coverage = {(c.source, c.side): c for c in integrated.coverage}
    assert coverage[(E.CAPACITY, Side.SOURCE)].state is CS.CURRENT
    assert coverage[(E.CAPACITY, Side.TARGET)].analysis_ids == (str(uuid.UUID(int=2)),)
    assert coverage[(E.SECURITY, Side.TARGET)].state is CS.STALE  # another content: never used
    assert coverage[(E.COST, Side.TARGET)].state is CS.MISSING
    assert {coverage[(E.SIMULATION, s)].state for s in Side} == {CS.UNSUPPORTED}  # explicit, not silent
    states = {(r.source, r.reference): r.state for r in integrated.evidence}
    assert states[(E.SECURITY, str(uuid.UUID(int=3)))] is EvidenceState.STALE
    assert {r.model_version for r in integrated.evidence} == {"1"}  # model versions preserved


def test_dimensions_without_current_target_evidence_are_stated_not_blocking() -> None:
    integrated = integrate(proposal(), [AnalysisEvidence(stored(E.VALIDATION))])
    missing = {f.key for f in integrated.findings if f.type is FindingType.MISSING_EVIDENCE}
    expected = ("capacity", "cost", "reliability", "security", "observability")
    assert missing == {f"evidence:{s}" for s in expected}
    assert integrated.status is PlanStatus.DRAFT


def test_without_analyses_nothing_is_invented() -> None:
    before, integrated = proposal(), integrate(proposal(), [])
    assert {c.key: c.status for c in integrated.checkpoints} == {c.key: c.status for c in before.checkpoints}
    assert [r.key for r in integrated.risks] == [r.key for r in before.risks]


def test_a_failed_analysis_is_never_evidence() -> None:
    integrated = integrate(proposal(), [AnalysisEvidence(stored(E.CAPACITY, status="failed"))])
    assert checkpoints(integrated)["capacity:target"].status is P.NOT_RUN
    assert not [r for r in integrated.evidence if r.source is E.CAPACITY]


# --- checkpoints -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("summary", "status"),
    [
        ({"total": 2, "blocking": 1, "by_severity": {"high": 1, "low": 1}}, P.FAIL),
        ({"total": 1, "blocking": 0, "by_severity": {"medium": 1}}, P.WARNING),
        ({"total": 0, "blocking": 0, "by_severity": {}}, P.PASS),
    ],
)
def test_the_target_validation_checkpoint_follows_its_run(
    summary: dict[str, Any], status: CheckpointStatus
) -> None:
    run = stored(E.VALIDATION)
    checkpoint = checkpoints(integrate(proposal(), [AnalysisEvidence(run, summary=summary)]))[
        "validation:target"
    ]
    assert (checkpoint.status, checkpoint.basis) == (status, CheckpointBasis.MODELED)
    assert [e.reference for e in checkpoint.evidence] == [str(run.analysis_id)]
    assert checkpoint.actual


def test_a_partial_analysis_never_passes() -> None:
    capacity = stored(E.CAPACITY, missing=("workload of db",))
    checkpoint = checkpoints(integrate(proposal(), [AnalysisEvidence(capacity)]))["capacity:target"]
    assert checkpoint.status is P.WARNING
    assert "workload of db" in (checkpoint.actual or "")


def test_only_stale_evidence_cannot_evaluate() -> None:
    checkpoint = checkpoints(integrate(proposal(), [AnalysisEvidence(stored(E.CAPACITY, "stale"))]))[
        "capacity:target"
    ]
    assert checkpoint.status is P.CANNOT_EVALUATE
    assert {e.state for e in checkpoint.evidence} == {EvidenceState.STALE}


def test_capacity_prerequisites_come_from_the_target_analysis() -> None:
    option = EvidenceItem(
        TriggerKind.SCALING_OPTION, "cpu", "api", "scaling:api:cpu:horizontal",
        (Evidence("current", "4"), Evidence("required", "6"), Evidence("unit", "replicas")),
        "Add 2 replicas of api.",
    )  # fmt: skip
    integrated = integrate(proposal(), [AnalysisEvidence(stored(E.CAPACITY, items=(option,)))])
    assert checkpoints(integrated)["capacity:target"].status is P.WARNING
    risk = risks(integrated)["capacity:target:api:cpu"]
    assert risk.status is RiskStatus.CONFIRMED
    assert "current 4, required 6 replicas" in risk.description  # the engine's figures, not computed here
    assert risk.mitigation == "Add 2 replicas of api."
    unscaled = EvidenceItem(TriggerKind.SCALING_UNSUPPORTED, "memory", "db", "bottleneck:db:memory")
    blocked = integrate(proposal(), [AnalysisEvidence(stored(E.CAPACITY, items=(unscaled,)))])
    assert checkpoints(blocked)["capacity:target"].status is P.FAIL


def test_a_security_regression_is_judged_against_the_source() -> None:
    shared, new = finding("tls_missing", "api", "f1"), finding("exposed", "db", "f2")
    analyses = [
        AnalysisEvidence(stored(E.SECURITY, "source", 1, items=(shared,))),
        AnalysisEvidence(stored(E.SECURITY, "target", 2, items=(shared, new))),
    ]
    integrated = integrate(proposal(), analyses)
    checkpoint = checkpoints(integrated)["security:target"]
    assert checkpoint.status is P.FAIL
    assert checkpoint.actual == "Finding(s) the source did not have: exposed on db."
    assert len(checkpoint.evidence) == 2  # both analyses it compared
    assert risks(integrated)["security:finding:f2"].status is RiskStatus.CONFIRMED
    alone = integrate(proposal(), analyses[1:])
    assert checkpoints(alone)["security:target"].status is P.WARNING  # never judged without the source
    assert "security:finding:f2" not in risks(alone)


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"checks": (RequirementCheck("r1", "violated", "k1"),)}, P.FAIL),
        ({"facts": (Evidence("availability.web", "0.99"),)}, P.WARNING),
        ({"facts": (Evidence("availability.web", "0.999"),)}, P.PASS),
    ],
)
def test_reliability_is_checked_against_requirements_and_the_source(
    fields: dict[str, Any], status: CheckpointStatus
) -> None:
    source = stored(E.RELIABILITY, "source", 1, facts=(Evidence("availability.web", "0.999"),))
    target = stored(E.RELIABILITY, "target", 2, **fields)
    integrated = integrate(proposal(), [AnalysisEvidence(source), AnalysisEvidence(target)])
    assert checkpoints(integrated)["reliability:target"].status is status


# --- cost ------------------------------------------------------------------------------------------


def priced(side: str, n: int, monthly: str, snapshot: str = "s1", complete: str = "true") -> AnalysisEvidence:
    facts = (Evidence("currency", "EUR"), Evidence("monthly", monthly), Evidence("complete", complete))
    return AnalysisEvidence(stored(E.COST, side, n, facts=facts), inputs={"snapshot_id": snapshot})


def test_a_cost_increase_is_stated_only_under_the_same_pricing() -> None:
    rising = integrate(proposal(), [priced("source", 1, "100"), priced("target", 2, "180")])
    assert risks(rising)["cost:target"].description == (
        "The modeled monthly cost rises from 100 to 180 EUR, with the same pricing snapshot."
    )
    for analyses, reason in (
        (
            [priced("source", 1, "100"), priced("target", 2, "180", snapshot="s2")],
            "different pricing snapshots",
        ),
        ([priced("source", 1, "100"), priced("target", 2, "180", complete="false")], "incomplete"),
    ):
        integrated = integrate(proposal(), analyses)
        assert "cost:target" not in risks(integrated)  # never across assumptions
        note = {(c.source, c.side): c.note for c in integrated.coverage}[(E.COST, Side.TARGET)]
        assert reason in (note or "")
    falling = integrate(proposal(), [priced("source", 1, "100"), priced("target", 2, "90")])
    assert "cost:target" not in risks(falling)


# --- candidates, staleness and determinism ---------------------------------------------------------


def test_a_candidate_target_cites_the_candidates_evidence_and_no_engine_analysis_of_it() -> None:
    proposed = candidate(SCALE_API)
    analysis_id = uuid.UUID(int=9)
    target, overlay = candidate_target(SOURCE, SOURCE_IR, analysis_id, proposed)
    request = MigrationRequest(ARCHITECTURE, 1, TargetSpec(analysis_id=analysis_id, candidate_id=proposed.id))
    context = PlanningContext(SOURCE_IR, overlay, analyze(SOURCE_IR, overlay, SOURCE, target), request)
    integrated = integrate(generate(context, default_registry()), [], proposed)
    coverage = {(c.source, c.side): c.state for c in integrated.coverage}
    assert coverage[(E.CAPACITY, Side.TARGET)] is CS.UNSUPPORTED
    assert set(proposed.evidence) <= set(integrated.evidence)  # the candidate's evidence, as cited
    assert {c.status for c in integrated.checkpoints if c.key.endswith(":target")} == {P.CANNOT_EVALUATE}
    assert not [f for f in integrated.findings if f.type is FindingType.MISSING_EVIDENCE]


def test_cited_evidence_drives_staleness_and_integration_is_deterministic() -> None:
    capacity = stored(E.CAPACITY, "target", 2)
    first = integrate(proposal(), [AnalysisEvidence(capacity)])
    assert first.to_dict() == integrate(proposal(), [AnalysisEvidence(capacity)]).to_dict()
    now = CurrentState(
        first.source.content_hash, first.target.content_hash, 2, dict(first.models),
        {capacity.ref(EvidenceState.CURRENT).key: EvidenceState.CURRENT},
    )  # fmt: skip
    assert not freshness(first, now).stale
    replaced = CurrentState(now.source_hash, now.target_hash, 2, now.models, {})
    assert freshness(first, replaced).reasons == (StaleReason.EVIDENCE_STALE,)
