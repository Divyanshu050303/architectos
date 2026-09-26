"""The evaluation harness itself: pairing, metrics and the regression gate are right."""

from typing import Any

from ai.evaluation.evaluator import Evaluation
from ai.evaluation.graders.requirements import grade
from ai.evaluation.regression import check


def candidate(
    type_: str, category: str, normalized: dict[str, Any] | None = None, scope: str = "system"
) -> dict[str, Any]:
    return {"type": type_, "category": category, "scope": scope, "normalized_data": normalized}


def rps(operator: str, value: str) -> dict[str, Any]:
    return {"metric": "requests_per_second", "operator": operator, "value": value, "unit": "requests/second"}


def result(candidates: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "candidates": candidates,
        "ambiguities": [],
        "assumptions": [],
        "issues": [],
        "conflicts": [],
        "completeness": {"status": "complete"},
        "ready_for_architecture": True,
    }
    return base | extra


def example(requirements: list[dict[str, Any]], **expected: Any) -> dict[str, Any]:
    labels = {
        "requirements": requirements,
        "ambiguities": [],
        "assumptions": [],
        "issues": [],
        "conflicts": 0,
    }
    return {"id": "x", "input": "…", "expected": labels | expected}


def test_pairing_prefers_the_exact_bound_of_the_same_metric() -> None:
    expected = [
        {"type": "capacity", "category": "throughput", **rps(">=", "10000")},
        {"type": "capacity", "category": "throughput", **rps("<=", "5000")},
    ]
    predicted = [
        candidate("capacity", "throughput", rps("<=", "5000")),
        candidate("capacity", "throughput", rps(">=", "10000")),
    ]
    g = grade(example(expected), result(predicted))
    assert (g.true_positives, g.false_positives, g.false_negatives, g.normalized) == (2, 0, 0, 2)


def test_misses_extras_and_mismatches_are_counted() -> None:
    expected = [
        {"type": "capacity", "category": "throughput", **rps(">=", "2000")},
        {"type": "security", "category": "encryption"},
    ]
    predicted = [
        candidate("performance", "throughput", rps(">=", "3000"), scope="api"),  # paired, but wrong
        candidate("functional", "order"),  # not expected
    ]
    g = grade(example(expected), result(predicted))
    assert (g.true_positives, g.false_positives, g.false_negatives) == (1, 1, 1)
    assert (g.classified, g.scoped, g.normalized) == (0, 0, 0)
    assert g.misses == ("security/encryption",)
    assert g.extras == ("functional/order",)
    assert len(g.mismatches) == 1


def test_finding_codes_conflicts_and_readiness() -> None:
    labels = example(
        [],
        ambiguities=["vague_traffic"],
        conflicts=1,
        completeness="incomplete",
        ready_for_architecture=False,
    )
    found = result(
        [],
        ambiguities=[{"kind": "ambiguity", "code": "vague_latency"}],
        conflicts=[
            {"kind": "conflict", "code": "disjoint_bounds"},
            {"kind": "consistency", "code": "strengthens"},
        ],
        completeness={"status": "incomplete"},
        ready_for_architecture=True,
    )
    evaluation = Evaluation((grade(labels, found),))
    metrics = evaluation.metrics()
    assert (metrics["ambiguities_precision"], metrics["ambiguities_recall"]) == (0.0, 0.0)
    assert metrics["conflict_accuracy"] == 1.0  # consistency notes are not conflicts
    assert (metrics["completeness_accuracy"], metrics["readiness_accuracy"]) == (1.0, 0.0)


def test_the_regression_gate() -> None:
    limits = {"extraction_recall": 0.9, "false_positives": 3}
    assert check({"extraction_recall": 0.95, "false_positives": 2}, limits) == []
    assert check({"extraction_recall": 0.85, "false_positives": 4}, limits) == [
        "extraction_recall: 0.85 is below the threshold 0.9",
        "false_positives: 4 is above the ceiling 3",
    ]
    assert check({}, limits) == ["extraction_recall: not measured", "false_positives: not measured"]
