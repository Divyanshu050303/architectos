"""Human-readable evaluation reports."""

from .evaluator import Evaluation


def render(evaluation: Evaluation) -> str:
    metrics = evaluation.metrics()
    lines = ["Requirements Engine evaluation", "", "| metric | value |", "|---|---|"]
    lines += [f"| {name} | {value:g} |" for name, value in metrics.items()]
    differences = []
    for g in evaluation.grades:
        parts = []
        if g.misses:
            parts.append(f"missed {list(g.misses)}")
        if g.extras:
            parts.append(f"extra {list(g.extras)}")
        parts += [f"mismatch {m}" for m in g.mismatches]
        for group, expected in g.expected_codes.items():
            found = g.predicted_codes[group]
            if expected != found:
                parts.append(f"{group} expected {sorted(expected)} found {sorted(found)}")
        if g.conflicts_found != g.conflicts_expected:
            parts.append(f"conflicts expected {g.conflicts_expected} found {g.conflicts_found}")
        if parts:
            differences.append(f"- {g.example_id}: " + "; ".join(parts))
    if differences:
        lines += ["", "Differences from the labels:", *differences]
    return "\n".join(lines)
