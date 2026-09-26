"""Grading one Requirements Engine result against one labelled example.

Expected requirements are labelled in *canonical* form (canonical unit and value), so equivalent
spellings grade the same ("1.5 seconds" and "1500 ms"). Pairing is deterministic:

- a quantitative expectation pairs with an unpaired prediction of the same metric, preferring an
  exact canonical match (so two bounds on one metric pair correctly);
- a qualitative expectation pairs with an unpaired prediction of the same type and category.

Unpaired expectations are false negatives, unpaired predictions false positives. On the pairs:
classification (type and category), scope, and normalization (operator, value or range, unit,
percentile, set values in canonical form).
"""

from dataclasses import dataclass, field
from typing import Any

_CANONICAL_FIELDS = ("operator", "value", "min", "max", "unit", "percentile", "values")


@dataclass(frozen=True, slots=True)
class Grade:
    example_id: str
    true_positives: int
    false_positives: int
    false_negatives: int
    classified: int  # pairs with the right type and category
    scoped: int  # pairs with the right scope
    quantitative_pairs: int
    normalized: int  # quantitative pairs with the exact canonical constraint
    expected_codes: dict[str, set[str]] = field(default_factory=dict)  # ambiguities, assumptions, issues
    predicted_codes: dict[str, set[str]] = field(default_factory=dict)
    conflicts_expected: int = 0
    conflicts_found: int = 0
    completeness: tuple[str, str] | None = None  # (expected, actual)
    ready: tuple[bool, bool] | None = None
    misses: tuple[str, ...] = ()  # human-readable, for reports
    extras: tuple[str, ...] = ()
    mismatches: tuple[str, ...] = ()  # paired, but classified, scoped or normalized differently


def _canonical(prediction: dict[str, Any]) -> dict[str, Any]:
    data = prediction.get("normalized_data") or {}
    return {k: data[k] for k in _CANONICAL_FIELDS if k in data}


def _expected_canonical(expected: dict[str, Any]) -> dict[str, Any]:
    return {k: expected[k] for k in _CANONICAL_FIELDS if k in expected}


def _metric(prediction: dict[str, Any]) -> str | None:
    data = prediction.get("normalized_data")
    return data.get("metric") if data else None


def _label(item: dict[str, Any]) -> str:
    detail = item.get("metric") or item.get("category")
    return f"{item['type']}/{item['category']}" + (f" ({detail})" if item.get("metric") else "")


def _pair(
    expected: list[dict[str, Any]], predicted: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    free = list(predicted)
    pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for item in expected:
        if "metric" in item:
            same_metric = [p for p in free if _metric(p) == item["metric"]]
            exact = [p for p in same_metric if _canonical(p) == _expected_canonical(item)]
            options = exact or same_metric
        else:
            wanted = (item["type"], item["category"])
            options = [p for p in free if _metric(p) is None and (p["type"], p["category"]) == wanted]
        chosen = options[0] if options else None
        if chosen is not None:
            free.remove(chosen)
        pairs.append((item, chosen))
    return pairs


def _codes(result: dict[str, Any], group: str) -> set[str]:
    kinds = {"issues": {"invalid", "rejected", "duplicate", "unresolved"}}
    findings = result[group]
    if group in kinds:
        findings = [f for f in findings if f["kind"] in kinds[group]]
    return {f["code"] for f in findings}


def grade(example: dict[str, Any], result: dict[str, Any]) -> Grade:
    expected = example["expected"]
    predicted = result["candidates"]
    pairs = _pair(expected["requirements"], predicted)
    matched = [(e, p) for e, p in pairs if p is not None]
    paired_ids = {id(p) for _, p in matched}
    quantitative = [(e, p) for e, p in matched if "metric" in e]
    conflicts_found = sum(f["kind"] == "conflict" for f in result["conflicts"])
    completeness = (
        (expected["completeness"], result["completeness"]["status"]) if "completeness" in expected else None
    )
    ready = (
        (expected["ready_for_architecture"], result["ready_for_architecture"])
        if "ready_for_architecture" in expected
        else None
    )
    return Grade(
        example_id=example["id"],
        true_positives=len(matched),
        false_positives=len(predicted) - len(matched),
        false_negatives=len(pairs) - len(matched),
        classified=sum((e["type"], e["category"]) == (p["type"], p["category"]) for e, p in matched),
        scoped=sum(e.get("scope", "system") == p["scope"] for e, p in matched),
        quantitative_pairs=len(quantitative),
        normalized=sum(_canonical(p) == _expected_canonical(e) for e, p in quantitative),
        expected_codes={g: set(expected[g]) for g in ("ambiguities", "assumptions", "issues")},
        predicted_codes={g: _codes(result, g) for g in ("ambiguities", "assumptions", "issues")},
        conflicts_expected=expected["conflicts"],
        conflicts_found=conflicts_found,
        completeness=completeness,
        ready=ready,
        misses=tuple(_label(e) for e, p in pairs if p is None),
        extras=tuple(_label(p) for p in predicted if id(p) not in paired_ids),
        mismatches=tuple(m for e, p in matched if (m := _mismatch(e, p))),
    )


def _mismatch(expected: dict[str, Any], predicted: dict[str, Any]) -> str:
    differences = []
    if (expected["type"], expected["category"]) != (predicted["type"], predicted["category"]):
        differences.append(f"class {predicted['type']}/{predicted['category']}")
    if expected.get("scope", "system") != predicted["scope"]:
        differences.append(f"scope {predicted['scope']}")
    if "metric" in expected and _canonical(predicted) != _expected_canonical(expected):
        differences.append(f"normalized {_canonical(predicted)}")
    return f"{_label(expected)}: " + ", ".join(differences) if differences else ""
