"""The vocabulary of drift detection: analysis statuses, compatibility outcomes, finding types and
classifications, identity-matching methods, review statuses and actions — and the checking helpers,
shared with discovery's (the same validators), raising drift's own error."""

from collections.abc import Iterable
from enum import StrEnum

from core.domain.discovery.values import (
    code,
    count,
    digest,
    fingerprint,
    items,
    json_value,
    key,
    text,
    texts,
)

from .errors import InvalidDriftResult

__all__ = [
    "check", "code", "count", "digest", "fingerprint", "items", "json_value", "key", "text", "texts",
]  # fmt: skip


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"  # coverage limits, potential or unknown findings
    FAILED = "failed"
    CANCELLED = "cancelled"
    INCOMPATIBLE_INPUTS = "incompatible_inputs"  # the inputs cannot be compared reliably: no findings


FINISHED = frozenset(
    {
        AnalysisStatus.COMPLETED,
        AnalysisStatus.COMPLETED_WITH_WARNINGS,
        AnalysisStatus.FAILED,
        AnalysisStatus.CANCELLED,
        AnalysisStatus.INCOMPATIBLE_INPUTS,
    }
)


class Compatibility(StrEnum):
    """Whether the baseline and the observed state can be compared — per dimension and overall, in
    order from best to worst."""

    COMPATIBLE = "compatible"
    COMPATIBLE_WITH_WARNINGS = "compatible_with_warnings"
    PARTIALLY_COMPARABLE = "partially_comparable"  # within the stated limitations only
    CANNOT_DETERMINE = "cannot_determine"  # the evidence needed to decide is missing
    INCOMPATIBLE = "incompatible"


COMPATIBILITY_ORDER = tuple(Compatibility)


def worst(outcomes: Iterable[Compatibility]) -> Compatibility:
    """The least comparable of ``outcomes`` (compatible when there are none)."""
    return max(outcomes, key=COMPATIBILITY_ORDER.index, default=Compatibility.COMPATIBLE)


class FindingType(StrEnum):
    COMPONENT_ADDED = "component_added"
    COMPONENT_REMOVED = "component_removed"
    COMPONENT_MODIFIED = "component_modified"  # its kind or catalog component
    CONNECTION_ADDED = "connection_added"
    CONNECTION_REMOVED = "connection_removed"
    CONNECTION_MODIFIED = "connection_modified"
    CONFIGURATION_CHANGED = "configuration_changed"
    RESOURCE_CHANGED = "resource_changed"  # replicas, CPU, memory, storage
    MAPPING_CHANGED = "mapping_changed"  # the catalog mapping of the same entity
    UNRESOLVED_DIFFERENCE = "unresolved_difference"  # identity or evidence too incomplete to classify
    COVERAGE_CHANGED = "coverage_changed"  # what was inspected differs from what the baseline rests on
    COMPARISON_INCOMPATIBLE = "comparison_incompatible"


class Classification(StrEnum):
    CONFIRMED = "confirmed"  # comparable baseline and discovery evidence support it
    POTENTIAL = "potential"  # evidence suggests it; identity or coverage is incomplete
    NOT_COMPARABLE = "not_comparable"  # input differences prevent a reliable comparison
    UNKNOWN = "unknown"  # the evidence is insufficient


class ElementType(StrEnum):
    NODE = "node"
    CONNECTION = "connection"
    SCOPE = "scope"  # the comparison itself: an incompatible input, an artifact outside coverage


class MatchMethod(StrEnum):
    """How a baseline element was matched to a discovered one — never by name."""

    SAME_ID = "same_id"  # the node id is the discovery key (a stable source identifier)
    CONFIRMED_MAPPING = "confirmed_mapping"  # a person confirmed the identity
    SIGNATURE = "signature"  # a connection: the same endpoints and kind
    AMBIGUOUS = "ambiguous"  # several candidates: unresolved until a person decides
    UNMATCHED = "unmatched"


class ReviewStatus(StrEnum):
    """A person's review of a drift item — separate from what the comparison found."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"  # seen; nothing about the architecture changes
    INVESTIGATING = "investigating"
    ACCEPTED = "accepted"  # expected; the baseline is not updated by it
    DISMISSED = "dismissed"  # with a reason
    RESOLVED = "resolved"  # tied to evidence: a later analysis or a linked revision
    REOPENED = "reopened"


class ReviewAction(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    INVESTIGATE = "investigate"
    ACCEPT = "accept"
    DISMISS = "dismiss"
    RESOLVE = "resolve"
    REOPEN = "reopen"
    NOTE = "note"  # an investigation note: the status is unchanged
    LINK = "link"  # a decision, migration plan, evolution analysis or revision: the status is unchanged
    DETECTED = "detected"  # recorded by an analysis that found the difference (again)


class LinkKind(StrEnum):
    DECISION = "decision"
    MIGRATION_PLAN = "migration_plan"
    EVOLUTION_ANALYSIS = "evolution_analysis"
    REVISION = "revision"


def check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidDriftResult(details={"fields": found})
