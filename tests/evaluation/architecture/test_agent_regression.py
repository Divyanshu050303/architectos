"""The architecture agent's guardrails, measured on the v1 scenarios: they must never regress, must be
deterministic, must be able to fail, and the set itself must stay well-formed."""

import asyncio
import json
from typing import Any

from ai.evaluation.architecture_agent import (
    CEILINGS,
    DATASET,
    Evaluation,
    check,
    evaluate,
    load_scenarios,
    run_scenario,
    thresholds,
)


def test_the_guardrails_do_not_regress() -> None:
    evaluation = evaluate()
    assert check(evaluation.metrics()) == [], evaluation.misses()


def test_the_evaluation_is_deterministic() -> None:
    first, second = evaluate(), evaluate()
    assert first.metrics() == second.metrics()
    hashes = [
        [o.run.candidate.content_hash if o.run.candidate else None for o in e.outcomes]
        for e in (first, second)
    ]
    assert hashes[0] == hashes[1]


def test_every_metric_has_a_threshold_and_ceilings_hold_at_zero() -> None:
    assert set(thresholds()) == set(evaluate().metrics()) - {"scenarios"}
    assert {name: thresholds()[name] for name in CEILINGS} == dict.fromkeys(CEILINGS, 0)


def _with(scenario: dict[str, Any], **expected: Any) -> dict[str, Any]:
    return scenario | {"expected": scenario["expected"] | expected}


def test_a_wrong_expectation_is_a_miss() -> None:
    """The metrics can fail: a scenario expected to end otherwise is counted as a miss."""
    scenarios = {s["id"]: s for s in load_scenarios()}
    ready = asyncio.run(run_scenario(_with(scenarios["api-relational-db"], status="failed")))
    refused = asyncio.run(run_scenario(_with(scenarios["prompt-injection"], rejections=["secret_in_output"])))
    metrics = Evaluation((ready, refused)).metrics()
    assert metrics["outcome_accuracy"] == 0.5
    assert metrics["rejection_accuracy"] == 0.0
    assert metrics["unsafe_candidates"] == 1  # a candidate where the (wrong) expectation says none
    assert check(metrics)


def _cited(scenario: dict[str, Any]) -> set[str]:
    outputs = [o for o in scenario["outputs"] if isinstance(o, dict)]
    claims = [c for o in outputs for c in o.get("patch", {}).get("claims", [])]
    return {e for c in claims for e in c.get("evidence", [])}


def test_the_set_is_well_formed_and_its_size_is_stated() -> None:
    scenarios = load_scenarios()
    assert len({s["id"] for s in scenarios}) == len(scenarios) == 10
    statuses = {s["expected"]["status"] for s in scenarios}
    assert statuses == {"candidate_ready", "awaiting_clarification", "failed"}
    for scenario in scenarios:
        assert scenario["requirements"], scenario["id"]
        assert scenario["outputs"], scenario["id"]
        given = {p["chunk"] for p in scenario.get("passages", ())}
        if scenario["expected"]["status"] == "candidate_ready":
            assert _cited(scenario) <= given, scenario["id"]  # a ready scenario cites only what it retrieves
    readme = (DATASET / "README.md").read_text(encoding="utf-8")
    assert "10 scenarios" in readme
    assert "## Known limits" in readme
    assert all(f"`{s['id']}`" in readme for s in scenarios)
    assert json.loads((DATASET / "thresholds.json").read_text(encoding="utf-8")) == thresholds()
