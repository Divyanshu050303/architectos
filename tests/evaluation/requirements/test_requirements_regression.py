"""The Requirements Engine's quality, measured on the labelled dataset: it must never regress."""

import json
from collections import Counter

from ai.evaluation.evaluator import DEFAULT_DATASET, evaluate, load
from ai.evaluation.regression import THRESHOLDS, check, thresholds
from core.domain.requirements.analyses import MAX_INPUT_CHARACTERS
from core.domain.requirements.enums import RequirementScope, RequirementType
from core.domain.requirements.requirements import METRICS
from core.domain.requirements.value_objects import CANONICAL_UNITS

LABELS = {
    "requirements",
    "ambiguities",
    "assumptions",
    "issues",
    "conflicts",
    "completeness",
    "ready_for_architecture",
}


async def test_quality_does_not_regress() -> None:
    metrics = (await evaluate()).metrics()
    assert check(metrics) == []


async def test_the_evaluation_is_deterministic() -> None:
    assert (await evaluate()).metrics() == (await evaluate()).metrics()


def test_every_metric_has_a_threshold() -> None:
    names = set(thresholds())
    required = {
        "extraction_precision",
        "extraction_recall",
        "classification_accuracy",
        "normalization_accuracy",
        "ambiguities_recall",
        "assumptions_recall",
        "false_positives",
        "false_negatives",
    }
    assert required <= names
    assert json.loads(THRESHOLDS.read_text()) == thresholds()


def test_the_dataset_is_well_formed() -> None:
    examples = load(DEFAULT_DATASET)
    assert len(examples) >= 50
    assert max(Counter(e["id"] for e in examples).values()) == 1
    canonical_units = set(CANONICAL_UNITS.values())
    for example in examples:
        assert 0 < len(example["input"]) <= MAX_INPUT_CHARACTERS
        expected = example["expected"]
        assert set(expected) <= LABELS
        for item in expected["requirements"]:
            assert item["type"] in {t.value for t in RequirementType}, example["id"]
            assert item.get("scope", "system") in {s.value for s in RequirementScope}, example["id"]
            if "metric" in item:
                assert item["metric"] in METRICS, example["id"]
                unit = item.get("unit")
                assert unit is None or unit in canonical_units or unit.endswith("/month"), (
                    example["id"],
                    unit,
                )


def test_the_dataset_covers_every_kind_of_case() -> None:
    examples = load(DEFAULT_DATASET)
    ids = " ".join(e["id"] for e in examples)
    for kind in ("rps", "latency", "availability", "rpo", "rto", "retention", "storage", "budget", "regions",
                 "vague", "conflict", "negative", "impossible", "gap_", "scenario_"):  # fmt: skip
        assert kind in ids, kind
    scenarios = [e for e in examples if e["id"].startswith("scenario_")]
    assert {e["id"] for e in scenarios} >= {
        "scenario_food_delivery", "scenario_ecommerce", "scenario_saas_dashboard", "scenario_fintech",
        "scenario_social", "scenario_iot_telemetry",
    }  # fmt: skip
