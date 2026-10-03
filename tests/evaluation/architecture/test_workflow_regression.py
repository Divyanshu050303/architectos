"""The autonomous architecture workflow, measured on the v1 workflows: the metrics must never regress,
must be deterministic, must each have something to measure, must be able to fail, and the set itself
must stay well-formed."""

import asyncio
import json
from dataclasses import replace
from typing import Any

from ai.evaluation.architecture_workflow import (
    CEILINGS,
    DATASET,
    Evaluation,
    check,
    evaluate,
    load_scenarios,
    run_scenario,
    thresholds,
)
from core.domain.architecture_workflow.values import CandidateOrigin


def test_the_metrics_do_not_regress() -> None:
    evaluation = evaluate()
    assert check(evaluation.metrics()) == [], evaluation.misses()


def test_the_evaluation_is_deterministic() -> None:
    first, second = evaluate(), evaluate()
    assert first.metrics() == second.metrics()
    hashes = [[[c.content_hash for c in o.candidates] for o in e.outcomes] for e in (first, second)]
    assert hashes[0] == hashes[1]


def test_every_metric_has_a_threshold_and_ceilings_hold_at_zero() -> None:
    assert set(thresholds()) == set(evaluate().metrics()) - {"scenarios"}
    assert {name: thresholds()[name] for name in CEILINGS} == dict.fromkeys(CEILINGS, 0)


def test_every_metric_has_something_to_measure() -> None:
    """No share is 1.0 for want of anything to grade."""
    outcomes = evaluate().outcomes
    candidates = [c for o in outcomes for c in o.candidates]
    assert any(c.origin is CandidateOrigin.RULE for c in candidates)  # grounded, resolved, traces kept
    assert any(c.evidence for c in candidates)  # citation integrity
    assert any(a for c in candidates for a in c.ir.assumptions)  # assumption disclosure
    assert any(o.requests for o in outcomes)  # injection containment
    assert any(o.denied_at is not None for o in outcomes)  # actions after revocation
    assert any(o.asked for o in outcomes)  # a person was asked
    assert any(o.workflow.selected for o in outcomes)  # review packages


def _with(scenario: dict[str, Any], **expected: Any) -> dict[str, Any]:
    return scenario | {"expected": scenario["expected"] | expected}


def test_a_wrong_expectation_is_a_miss() -> None:
    scenarios = {s["id"]: s for s in load_scenarios()}
    reviewed = asyncio.run(run_scenario(_with(scenarios["goal-to-review"], status="failed")))
    improved = asyncio.run(run_scenario(_with(scenarios["rule-improvements"], origins=["agent"])))
    metrics = Evaluation((reviewed, improved)).metrics()
    assert metrics["outcome_accuracy"] == 0.0
    assert check(metrics)


def test_the_graders_can_fail() -> None:
    """A simulation report without a stated scenario, or an improvement whose parent never had the
    finding, are counted — the ceilings and shares are not vacuous."""
    scenarios = {s["id"]: s for s in load_scenarios()}
    simulated = asyncio.run(run_scenario(scenarios["simulation-scenario"]))
    flow = simulated.workflow
    unstated = replace(simulated, workflow=replace(flow, goal=replace(flow.goal, scenario=None)))
    improved = asyncio.run(run_scenario(scenarios["rule-improvements"]))
    first, *rest = improved.candidates
    kept = tuple(r for r in first.reports if r.engine != "reliability")  # the parent's findings gone
    orphaned = replace(improved, candidates=(replace(first, reports=kept), *rest))
    metrics = Evaluation((unstated, orphaned)).metrics()
    assert metrics["fabricated_analyses"] == 1
    assert metrics["grounded_triggers"] < 1.0
    assert check(metrics)


def test_the_set_is_well_formed_and_its_size_is_stated() -> None:
    scenarios = load_scenarios()
    assert len({s["id"] for s in scenarios}) == len(scenarios) == 15
    statuses = {s["expected"]["status"] for s in scenarios}
    assert statuses == {"review_ready", "needs_input", "failed"}
    for scenario in scenarios:
        assert scenario["goal"]["objective"], scenario["id"]
        assert ("requirements" in scenario) != ("extracted" in scenario), scenario["id"]
    readme = (DATASET / "README.md").read_text(encoding="utf-8")
    assert "15 scenarios" in readme
    assert "## Known limits" in readme
    assert all(f"`{s['id']}`" in readme for s in scenarios)
    assert all(f"`{name}`" in readme for name in thresholds())
    assert json.loads((DATASET / "thresholds.json").read_text(encoding="utf-8")) == thresholds()
