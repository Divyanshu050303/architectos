"""A drift finding: one difference — or one thing that could not be compared — between an exact
baseline revision and a discovery run, with what it rests on.

A finding names the compared elements (the baseline node or connection, the discovered entity or
relationship, the property path), both values (a secret's never: ``redacted``), how the elements were
matched, the discovery findings and source locations that are its evidence, the baseline element's
own provenance, and the comparison rule and version. Its ``classification`` says how far the evidence
goes (``confirmed``, ``potential``, ``not_comparable``, ``unknown``); its ``limitations`` what is left
open. There is no severity: no documented policy supports one.

Ids are stable and analysis-independent: the same difference in a later analysis has the same ``id``
(type, subject, path) and the same ``item_key`` (subject and path, whatever the type) — the key a
drift item correlates repeated findings by.
"""

from dataclasses import dataclass
from typing import Any

from core.domain.discovery.values import MAX_REFERENCE
from core.domain.evolution.values import EvidenceSource, EvidenceState

from .values import (
    Classification,
    Compatibility,
    ElementType,
    FindingType,
    MatchMethod,
    check,
    code,
    digest,
    json_value,
    key,
    text,
    texts,
)

MAX_SUBJECT = 512
MAX_ITEMS = 50
REMOVED = frozenset({FindingType.COMPONENT_REMOVED, FindingType.CONNECTION_REMOVED})
ADDED = frozenset({FindingType.COMPONENT_ADDED, FindingType.CONNECTION_ADDED})
VALUED = frozenset(
    {
        FindingType.CONFIGURATION_CHANGED,
        FindingType.RESOURCE_CHANGED,
        FindingType.COMPONENT_MODIFIED,
        FindingType.CONNECTION_MODIFIED,
        FindingType.MAPPING_CHANGED,
    }
)


@dataclass(frozen=True, slots=True)
class ImpactRef:
    """Context, not a claim: an engine whose model reads what the finding concerns, why (``basis``),
    and the stored analysis of the baseline revision by that engine — ``current`` (of its exact
    content), ``stale`` (of another revision or content: not to be relied on) or ``missing``. The
    analysis describes the baseline; nothing is recomputed for the discovered state."""

    engine: EvidenceSource
    basis: str
    state: EvidenceState
    analysis_id: str | None = None
    revision_number: int | None = None
    items: tuple[str, ...] = ()  # the analysis's own items about the same element

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.engine, EvidenceSource) else "impact.engine",
                text(self.basis, "impact.basis"),
                None if isinstance(self.state, EvidenceState) else "impact.state",
                text(self.analysis_id, "impact.analysis_id", 64, required=False),
                texts(self.items, "impact.items", MAX_REFERENCE),
                "impact.analysis_id"
                if (self.state is EvidenceState.MISSING) != (self.analysis_id is None)
                else None,
                "impact.items" if len(self.items) > MAX_ITEMS else None,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine.value,
            "basis": self.basis,
            "state": self.state.value,
            "analysis_id": self.analysis_id,
            "revision_number": self.revision_number,
            "items": list(self.items),
        }


@dataclass(frozen=True, slots=True)
class DriftFinding:
    type: FindingType
    classification: Classification
    element: ElementType
    subject: str  # what is compared, e.g. "node:db", "connection:api-[data_access]->db"
    explanation: str
    rule: str  # the comparison rule and its version, e.g. "drift-components@1"
    path: str | None = None  # the compared property, e.g. "configuration.replicas", "kind"
    baseline_id: str | None = None  # the baseline node or connection id
    discovered_key: str | None = None  # the discovered entity key or relationship id
    match: MatchMethod | None = None
    baseline_value: Any = None  # canonical JSON; None: absent (or redacted)
    discovered_value: Any = None
    redacted: bool = False  # a secret: that it changed is stated, its values never
    evidence: tuple[str, ...] = ()  # discovery finding ids
    locations: tuple[str, ...] = ()  # source references, "path#document:pointer"
    baseline_reference: str | None = None  # the baseline element's own provenance reference
    limitations: tuple[str, ...] = ()
    impact: tuple[ImpactRef, ...] = ()  # context from the other engines: never part of its identity
    # The requirements and decisions the baseline element references ("requirement:<id>[@<version>]",
    # "decision:<id>") — named for review, never judged violated or invalid.
    references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        confirmed = self.classification is Classification.CONFIRMED
        values = (self.baseline_value, self.discovered_value)
        check(
            [
                None if isinstance(self.type, FindingType) else "finding.type",
                None if isinstance(self.classification, Classification) else "finding.classification",
                None if isinstance(self.element, ElementType) else "finding.element",
                text(self.subject, "finding.subject", MAX_SUBJECT),
                text(self.explanation, "finding.explanation"),
                text(self.rule, "finding.rule", 64),
                text(self.path, "finding.path", MAX_REFERENCE, required=False),
                key(self.baseline_id, "finding.baseline_id", required=False),
                text(self.discovered_key, "finding.discovered_key", MAX_REFERENCE, required=False),
                None if self.match is None or isinstance(self.match, MatchMethod) else "finding.match",
                json_value(self.baseline_value, "finding.baseline_value"),
                json_value(self.discovered_value, "finding.discovered_value"),
                None if isinstance(self.redacted, bool) else "finding.redacted",
                texts(self.evidence, "finding.evidence", 64),
                texts(self.locations, "finding.locations", MAX_REFERENCE),
                text(self.baseline_reference, "finding.baseline_reference", MAX_REFERENCE, required=False),
                texts(self.limitations, "finding.limitations"),
                None
                if isinstance(self.impact, tuple) and all(isinstance(i, ImpactRef) for i in self.impact)
                else "finding.impact",
                texts(self.references, "finding.references", 128),
                "finding.redacted" if self.redacted and values != (None, None) else None,  # never kept
                # What was removed is a baseline element; what was added, a discovered one.
                "finding.baseline_id" if self.type in REMOVED and self.baseline_id is None else None,
                "finding.discovered_key" if self.type in ADDED and self.discovered_key is None else None,
                "finding.path" if self.type in VALUED and self.path is None else None,
                # A confirmed finding rests on evidence: discovery findings or the baseline's provenance.
                "finding.evidence"
                if confirmed and not self.evidence and self.baseline_reference is None
                else None,
                # What is not confirmed says what is missing.
                "finding.limitations" if not confirmed and not self.limitations else None,
            ]
        )
        object.__setattr__(self, "evidence", tuple(sorted(set(self.evidence))))
        object.__setattr__(self, "locations", tuple(sorted(set(self.locations))))

    @property
    def id(self) -> str:
        """Stable across analyses: the same difference has the same id."""
        return digest("dft", self.type.value, self.subject, self.path)

    @property
    def item_key(self) -> str:
        """What repeated findings are correlated by: the subject and property, whatever the type."""
        return digest("dfi", self.element.value, self.subject, self.path)

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.subject, self.path or "", self.type.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "item_key": self.item_key,
            "type": self.type.value,
            "classification": self.classification.value,
            "element": self.element.value,
            "subject": self.subject,
            "explanation": self.explanation,
            "rule": self.rule,
            "path": self.path,
            "baseline_id": self.baseline_id,
            "discovered_key": self.discovered_key,
            "match": self.match.value if self.match else None,
            "baseline_value": self.baseline_value,
            "discovered_value": self.discovered_value,
            "redacted": self.redacted,
            "evidence": list(self.evidence),
            "locations": list(self.locations),
            "baseline_reference": self.baseline_reference,
            "limitations": list(self.limitations),
            "impact": [i.to_dict() for i in self.impact],
            "references": list(self.references),
        }


@dataclass(frozen=True, slots=True)
class CompatibilityCheck:
    """One compatibility dimension and its outcome, with what it rests on."""

    dimension: str  # e.g. "ir_schema", "extractor_versions", "source_coverage", "identity"
    outcome: Compatibility
    message: str

    def __post_init__(self) -> None:
        check(
            [
                code(self.dimension, "compatibility.dimension"),
                None if isinstance(self.outcome, Compatibility) else "compatibility.outcome",
                text(self.message, "compatibility.message"),
            ]
        )

    def to_dict(self) -> dict[str, str]:
        return {"dimension": self.dimension, "outcome": self.outcome.value, "message": self.message}
