"""Runs the Requirements Engine over a labelled dataset and measures it.

    python -m ai.evaluation.evaluator                 # the report, as text
    python -m ai.evaluation.evaluator --json          # the metrics, as JSON
    python -m ai.evaluation.evaluator --check         # exit 1 below the recorded thresholds

Deterministic by default (the rules only), so the same code always scores the same; with a semantic
extractor the same harness measures what a model adds or breaks.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engines.requirements.service import RequirementsEngine

from .graders.requirements import Grade, grade

DATASETS = Path(__file__).parent / "datasets" / "requirements"
DEFAULT_DATASET = DATASETS / "v1.jsonl"


def load(path: Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as lines:
        return [json.loads(line) for line in lines if line.strip()]


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 1.0


@dataclass(frozen=True, slots=True)
class Evaluation:
    grades: tuple[Grade, ...]

    def metrics(self) -> dict[str, float]:
        g = self.grades
        tp, fp, fn = (
            sum(x.true_positives for x in g),
            sum(x.false_positives for x in g),
            sum(x.false_negatives for x in g),
        )
        precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
        metrics = {
            "extraction_precision": precision,
            "extraction_recall": recall,
            "extraction_f1": round(2 * precision * recall / (precision + recall), 4)
            if precision + recall
            else 0.0,
            "classification_accuracy": _ratio(sum(x.classified for x in g), tp),
            "scope_accuracy": _ratio(sum(x.scoped for x in g), tp),
            "normalization_accuracy": _ratio(
                sum(x.normalized for x in g), sum(x.quantitative_pairs for x in g)
            ),
            "conflict_accuracy": _ratio(sum(x.conflicts_found == x.conflicts_expected for x in g), len(g)),
        }
        for group in ("ambiguities", "assumptions", "issues"):
            expected = {(x.example_id, c) for x in g for c in x.expected_codes[group]}
            found = {(x.example_id, c) for x in g for c in x.predicted_codes[group]}
            metrics[f"{group}_precision"] = _ratio(len(expected & found), len(found))
            metrics[f"{group}_recall"] = _ratio(len(expected & found), len(expected))
        completeness = [x.completeness for x in g if x.completeness]
        ready = [x.ready for x in g if x.ready]
        metrics["completeness_accuracy"] = _ratio(sum(e == a for e, a in completeness), len(completeness))
        metrics["readiness_accuracy"] = _ratio(sum(e == a for e, a in ready), len(ready))
        metrics["false_positives"] = float(fp)
        metrics["false_negatives"] = float(fn)
        return metrics


async def evaluate(
    engine: RequirementsEngine | None = None, dataset: Sequence[dict[str, Any]] | None = None
) -> Evaluation:
    engine = engine or RequirementsEngine()
    examples = list(dataset) if dataset is not None else load()
    grades = []
    for example in examples:
        output = await engine.analyze(example["input"], [])
        grades.append(grade(example, output.result))
    return Evaluation(tuple(grades))


def main(argv: Sequence[str] | None = None) -> int:
    from .regression import check  # noqa: PLC0415 - the CLI only
    from .reports import render  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true", help="print the metrics as JSON")
    parser.add_argument("--check", action="store_true", help="exit 1 if a metric is below its threshold")
    args = parser.parse_args(argv)
    evaluation = asyncio.run(evaluate())
    metrics = evaluation.metrics()
    print(json.dumps(metrics, indent=2) if args.json else render(evaluation))  # noqa: T201 - a CLI
    failures = check(metrics)
    for failure in failures:
        print(f"REGRESSION: {failure}", file=sys.stderr)  # noqa: T201
    return 1 if args.check and failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
