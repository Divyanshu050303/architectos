"""Conflict and consistency detection for new candidates, among themselves and against the
project's existing requirements (existing-versus-existing is the project analysis's job).

Built on the domain's like-for-like comparison (``compare_like_with_like``): the same metric,
scope, percentile and canonical unit, compared as exact intervals. For each comparable pair:

- **disjoint** → a CONFLICT, always *blocking*: no value satisfies both ("RPS >= 10,000" and
  "RPS <= 5,000"; two region sets with nothing in common).
- **one stricter** → CONSISTENCY, *info*: the stricter one supersedes the other ("latency <= 100 ms"
  strengthens "<= 300 ms"; "availability >= 99.99 %" strengthens ">= 99.9 %"). Not a conflict.
- **equal** → CONSISTENCY, *info*: the same requirement in other words ("2 minutes", "120 s"),
  or already required by an existing requirement.
- **overlapping** (">= 100" and "<= 500") → nothing: together they simply form a range.

Tensions between different metrics (99.99 % availability on a 100 USD/month budget) are hard, not
contradictory, and are never reported here.
"""

from dataclasses import dataclass

from core.domain.requirements.analysis import Comparison, Relation, Severity, compare_like_with_like
from core.domain.requirements.candidates import RequirementCandidate
from core.domain.requirements.entities import Requirement, RequirementContent

from .findings import Finding, FindingKind


@dataclass(frozen=True, slots=True)
class Existing:
    """An existing requirement of the project, at the version being compared against."""

    reference: str  # REQ-3
    version: int
    content: RequirementContent

    @classmethod
    def of(cls, requirement: Requirement) -> Existing:
        return cls(requirement.reference, requirement.version, requirement.content)

    @property
    def label(self) -> str:
        return f"{self.reference}@v{self.version}"


type _Item = RequirementCandidate | Existing


def _name(item: _Item) -> str:
    if isinstance(item, Existing):
        return f"{item.reference} ({item.content.title})"
    text = item.span.text if item.span else item.content.title
    return f"“{text}”"


def _finding(
    kind: FindingKind,
    code: str,
    severity: Severity,
    message: str,
    suggestion: str,
    metric: str,
    *items: _Item,
) -> Finding:
    """A finding about ``items``: new candidates by key (and the first one's span), existing
    requirements by reference and version."""
    candidates = [i for i in items if isinstance(i, RequirementCandidate)]
    return Finding(
        kind,
        code,
        severity,
        message,
        candidate_keys=tuple(c.key for c in candidates),
        requirement_references=tuple(i.label for i in items if isinstance(i, Existing)),
        span=candidates[0].span if candidates else None,
        suggestion=suggestion,
        metric=metric,
    )


def _conflict(comparison: Comparison[_Item]) -> Finding:
    first, second, metric = comparison.first, comparison.second, comparison.metric
    if comparison.sets:
        message = f"{_name(first)} and {_name(second)} allow no {metric} in common."
    else:
        message = (
            f"{_name(first)} requires {metric} {comparison.first_bound}, but {_name(second)} requires "
            f"{metric} {comparison.second_bound}: no value satisfies both."
        )
    suggestion = "Decide which one holds, and change or drop the other."
    return _finding(
        FindingKind.CONFLICT, comparison.reason, Severity.BLOCKING, message, suggestion, metric, first, second
    )


def _stricter(comparison: Comparison[_Item]) -> Finding:
    first_is_stricter = comparison.relation is Relation.FIRST_STRICTER
    strict, loose = (
        (comparison.first, comparison.second) if first_is_stricter else (comparison.second, comparison.first)
    )
    strict_bound, loose_bound = (
        (comparison.first_bound, comparison.second_bound)
        if first_is_stricter
        else (comparison.second_bound, comparison.first_bound)
    )
    message = f"{_name(strict)} ({strict_bound}) is stricter than {_name(loose)} ({loose_bound})"
    if isinstance(loose, Existing):
        code, suggestion = (
            "tightens_existing",
            f"Promoting it tightens {loose.reference}: update or deprecate it.",
        )
        message += f": it would tighten {loose.reference}."
    elif isinstance(strict, Existing):
        code, suggestion = "already_covered", f"{strict.reference} already requires more; no need to promote."
        message += "; it is already covered."
    else:
        code, suggestion = "strengthens", "Keep the stricter one."
        message += ", so it supersedes it."
    return _finding(
        FindingKind.CONSISTENCY, code, Severity.INFO, message, suggestion, comparison.metric, strict, loose
    )


def _equal(comparison: Comparison[_Item]) -> Finding:
    first, second = comparison.first, comparison.second
    existing = next((i for i in (first, second) if isinstance(i, Existing)), None)
    if existing is not None:
        code, suggestion = (
            "already_required",
            f"{existing.reference} already requires this; no need to promote.",
        )
    else:
        code, suggestion = "equivalent", "Keep one of them."
    message = (
        f"{_name(first)} and {_name(second)} require the same {comparison.metric} ({comparison.first_bound})."
    )
    return _finding(
        FindingKind.CONSISTENCY, code, Severity.INFO, message, suggestion, comparison.metric, first, second
    )


def find_conflicts(
    candidates: tuple[RequirementCandidate, ...], existing: tuple[Existing, ...] = ()
) -> list[Finding]:
    """Conflicts (blocking) and consistency notes (info) involving at least one new candidate."""
    items: list[tuple[_Item, RequirementContent]] = [(c, c.content) for c in candidates]
    items += [(e, e.content) for e in existing]
    findings: list[Finding] = []
    for comparison in compare_like_with_like(items):
        if isinstance(comparison.first, Existing) and isinstance(comparison.second, Existing):
            continue
        match comparison.relation:
            case Relation.DISJOINT:
                findings.append(_conflict(comparison))
            case Relation.FIRST_STRICTER | Relation.SECOND_STRICTER:
                findings.append(_stricter(comparison))
            case Relation.EQUAL:
                findings.append(_equal(comparison))
            case Relation.OVERLAP:
                pass
    return findings
