"""Following differences across analyses, and what may resolve them.

**Correlation**: a completed analysis's findings are folded into the architecture's drift items by
their ``item_key`` (subject and property) — documented and stable, never by name or position. A
finding with no item opens one; a finding with an item is recorded on it (an analysis-made
``detected`` event; a resolved item reopens). A later analysis never erases an earlier one: items not
detected again are left as they are — their history is kept, and only a person resolves them.
Findings about the comparison itself (``scope``: an incompatible input, an artifact left uncompared)
and ``not_comparable`` ones are not items: they describe what could not be compared, not a
difference, and stay with their analysis.

**Resolution evidence**: an analysis resolves an item only if it is of the same architecture, it
compared its inputs (not incompatible), it no longer detects the item's difference, and it read
completely every artifact the item's findings came from — otherwise its silence says nothing.
"""

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from .analyses import DriftAnalysis
from .findings import DriftFinding
from .items import DriftItem
from .values import AnalysisStatus, Classification, Compatibility, ElementType

COMPARED = frozenset({AnalysisStatus.COMPLETED, AnalysisStatus.COMPLETED_WITH_WARNINGS})


def reviewable(finding: DriftFinding) -> bool:
    """A difference a person can review — not a statement about what could not be compared."""
    return (
        finding.element is not ElementType.SCOPE
        and finding.classification is not Classification.NOT_COMPARABLE
    )


@dataclass(frozen=True)
class Correlation:
    created: tuple[DriftItem, ...] = ()
    updated: tuple[DriftItem, ...] = ()


def correlate(
    items: Iterable[DriftItem],
    analysis: DriftAnalysis,
    at: datetime,
    new_id: Callable[[], uuid.UUID] = uuid.uuid7,
) -> Correlation:
    """The analysis's reviewable findings folded into the architecture's items."""
    if analysis.status not in COMPARED or analysis.result is None:
        return Correlation()
    architecture = analysis.request.architecture_id
    existing = {i.key: i for i in items if i.architecture_id == architecture}
    created: list[DriftItem] = []
    updated: list[DriftItem] = []
    for finding in analysis.result.findings:
        if not reviewable(finding):
            continue
        item = existing.get(finding.item_key)
        if item is None:
            fresh = DriftItem.first_seen(
                new_id(), analysis.project_id, architecture, finding, analysis.id, at
            )
            existing[finding.item_key] = fresh
            created.append(fresh)
        elif item.last_analysis_id != analysis.id:
            seen = item.detected(finding, analysis.id, at)
            existing[finding.item_key] = seen
            updated.append(seen)
    return Correlation(tuple(created), tuple(updated))


def resolution_problem(item: DriftItem, analysis: DriftAnalysis) -> str | None:
    """Why ``analysis`` cannot resolve ``item`` — None when it can."""
    if analysis.request.architecture_id != item.architecture_id or analysis.project_id != item.project_id:
        return "another_architecture"
    result = analysis.result
    if analysis.status not in COMPARED or result is None or result.status is Compatibility.INCOMPATIBLE:
        return "not_compared"
    if any(f.item_key == item.key for f in result.findings):
        return "still_detected"
    if not set(item.artifacts) <= set(result.coverage.inspected):
        return "outside_coverage"  # its silence says nothing about what it did not read
    return None
