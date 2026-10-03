"""The architecture diff, measured on the v1 pairs: it must never regress, must be deterministic, must be
able to fail, and the set itself must stay well-formed."""

import asyncio
import json
from typing import Any

from ai.evaluation.architecture_diff import (
    CEILINGS,
    DATASET,
    Evaluation,
    check,
    evaluate,
    load_scenarios,
    run_scenario,
    thresholds,
)


def test_the_diff_does_not_regress() -> None:
    evaluation = evaluate()
    assert check(evaluation.metrics()) == [], evaluation.misses()


def test_the_evaluation_is_deterministic() -> None:
    first, second = evaluate(), evaluate()
    assert first.metrics() == second.metrics()
    for one, two in zip(first.outcomes, second.outcomes, strict=True):
        assert one.diff.semantic == two.diff.semantic
        assert one.diff.engines == two.diff.engines
        assert one.run.explanation == two.run.explanation


def test_every_metric_has_a_threshold_and_ceilings_hold_at_zero() -> None:
    assert set(thresholds()) == set(evaluate().metrics()) - {"scenarios"}
    assert {name: thresholds()[name] for name in CEILINGS} == dict.fromkeys(CEILINGS, 0)


def _with(scenario: dict[str, Any], **expected: Any) -> dict[str, Any]:
    return scenario | {"expected": scenario["expected"] | expected}


def test_a_wrong_expectation_is_a_miss() -> None:
    """The metrics can fail: a scenario expected to end otherwise is counted as a miss."""
    scenarios = {s["id"]: s for s in load_scenarios()}
    scaling = scenarios["scaling-replicas"]
    wrong_changes = _with(scaling, changes=[["node", "db", "modified"]], groups=[["db"]])
    refused = _with(
        scaling, explanation={"status": "failed", "rejections": ["score_claim"], "model_calls": 1}
    )
    leaky = _with(scenarios["secret-rotation"], secrets=["Orders API"])  # a value that is shown
    outcomes = tuple(asyncio.run(run_scenario(s)) for s in (wrong_changes, refused, leaky))
    metrics = Evaluation(outcomes).metrics()
    assert metrics["change_accuracy"] < 1
    assert metrics["grouping_accuracy"] < 1
    assert metrics["rejection_accuracy"] == 0.0
    assert metrics["unsupported_explanations"] == 1  # completed where the (wrong) expectation says refused
    assert metrics["secret_leaks"] == 1
    assert check(metrics)


def test_the_set_is_well_formed_and_its_size_is_stated() -> None:
    scenarios = load_scenarios()
    assert len({s["id"] for s in scenarios}) == len(scenarios) == 11
    statuses = {s["expected"]["explanation"]["status"] for s in scenarios}
    assert statuses == {"completed", "failed", "not_needed"}
    names = {"scaling", "migration", "cache", "security", "no-change"}  # the cases the milestone names
    assert all(any(n in s["id"] for s in scenarios) for n in names)
    for scenario in scenarios:
        assert "changes" in scenario["expected"], scenario["id"]
        assert scenario["outputs"] or scenario["expected"]["explanation"]["status"] == "not_needed"
    readme = (DATASET / "README.md").read_text(encoding="utf-8")
    assert "11 scenarios" in readme
    assert "## Known limits" in readme
    assert all(f"`{s['id']}`" in readme for s in scenarios)
    assert json.loads((DATASET / "thresholds.json").read_text(encoding="utf-8")) == thresholds()
