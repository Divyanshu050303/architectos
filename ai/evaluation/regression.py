"""The regression gate: every metric must stay at or above its recorded threshold.

Thresholds (``datasets/requirements/thresholds.json``) are the floor the engine has reached. They
only move up: when the engine improves, raise them in the same change, so a later regression
cannot hide behind an old, lower number. ``false_positives`` and ``false_negatives`` are ceilings.
"""

import json
from pathlib import Path

THRESHOLDS = Path(__file__).parent / "datasets" / "requirements" / "thresholds.json"
_CEILINGS = {"false_positives", "false_negatives"}


def thresholds(path: Path = THRESHOLDS) -> dict[str, float]:
    loaded: dict[str, float] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def check(metrics: dict[str, float], limits: dict[str, float] | None = None) -> list[str]:
    limits = limits if limits is not None else thresholds()
    failures = []
    for name, limit in limits.items():
        value = metrics.get(name)
        if value is None:
            failures.append(f"{name}: not measured")
        elif name in _CEILINGS and value > limit:
            failures.append(f"{name}: {value:g} is above the ceiling {limit:g}")
        elif name not in _CEILINGS and value < limit:
            failures.append(f"{name}: {value:g} is below the threshold {limit:g}")
    return failures
