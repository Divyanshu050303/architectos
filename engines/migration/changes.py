"""Change analysis: the Architecture IR's diff of the exact source and target, classified for
migration planning. The diff itself is the IR's (``core.architecture_ir.diff``): elements matched by
their stable ids, fields compared as the IR serializes them, secret values redacted. This module
only reads it — neither architecture is modified, and nothing here compares graphs.

Targets: a later exact revision of the same architecture (read from its history), or an evolution
candidate applied to its exact baseline in memory — the source revision — with the evolution
overlay step (``apply_candidate``). A candidate on another baseline, one its validation refused, or
one that cannot be applied is refused, never approximated.
"""

import uuid
from collections.abc import Iterable

from core.architecture_ir.component import NodeKind
from core.architecture_ir.diff import ArchitectureDiff, ChangeKind, ElementChange, FieldChange, diff
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain.evolution.candidates import Candidate
from core.domain.evolution.errors import InvalidCandidate
from core.domain.evolution.overlays import apply_candidate
from core.domain.evolution.values import ValidationState
from core.domain.migrations.changes import Aspect, ChangeAnalysis, Interpretation, MigrationChange, Relevance
from core.domain.migrations.errors import InvalidMigrationRequest
from core.domain.migrations.plans import PlanFinding, SourceRef, TargetRef
from core.domain.migrations.steps import Trace
from core.domain.migrations.values import FindingType, TargetKind, TraceKind
from core.domain.observability import inputs as observability
from core.domain.security import inputs as security
from core.domain.simulations.catalog import CAPACITY_PROPERTIES, COST_PROPERTIES, RELIABILITY_PROPERTIES

STATEFUL = frozenset(
    {NodeKind.DATABASE.value, NodeKind.CACHE.value, NodeKind.QUEUE.value, NodeKind.STORAGE.value}
)
BOUNDARY = NodeKind.BOUNDARY.value
PLACEMENT = frozenset({"region", "availability_zones", "multi_az"})
SCALING = frozenset(
    {
        "replicas",
        "min_healthy_replicas",
        "autoscaling_min_replicas",
        "autoscaling_max_replicas",
        "autoscaling_target_cpu_ratio",
    }
)
SECURITY = frozenset(
    {*security.NODE_PROPERTIES, *security.CONNECTION_PROPERTIES, *security.BOUNDARY_PROPERTIES}
)
OBSERVABILITY = frozenset({*observability.NODE_PROPERTIES, *observability.CONNECTION_PROPERTIES})
# The engines that read a configuration property: each engine's own declared inputs.
ENGINE_INPUTS: dict[str, frozenset[str]] = {
    "capacity": frozenset(CAPACITY_PROPERTIES),
    "cost": frozenset(COST_PROPERTIES),
    "reliability": frozenset(RELIABILITY_PROPERTIES),
    "security": SECURITY,
    "observability": OBSERVABILITY,
}
GROUPS = (
    (SCALING, Aspect.SCALING),
    (SECURITY, Aspect.SECURITY),
    (OBSERVABILITY, Aspect.OBSERVABILITY),
    (PLACEMENT, Aspect.PLACEMENT),
)
METADATA_CATEGORIES = frozenset({"metadata", "provenance", "description", "lifecycle"})
DESCRIPTIVE = frozenset({Aspect.METADATA, Aspect.TRACEABILITY})
UNSUPPORTED, MANUAL = Interpretation.UNSUPPORTED, Interpretation.MANUAL_INTERPRETATION
NOT_ON_SOURCE = {"field": "target.candidate_id", "reason": "not_on_the_source"}


def revision_target(source: SourceRef, number: int, content: str) -> TargetRef:
    return TargetRef(TargetKind.REVISION, source.architecture_id, number, content)


def candidate_target(
    source: SourceRef, ir: ArchitectureIR, analysis_id: uuid.UUID, candidate: Candidate
) -> tuple[TargetRef, ArchitectureIR]:
    """The candidate applied in memory to the source revision (``ir``), and its reference."""
    baseline = candidate.baseline
    expected = (source.architecture_id, source.revision_number, source.content_hash)
    if (baseline.architecture_id, baseline.revision_number, baseline.content_hash) != expected:
        raise InvalidMigrationRequest(details=NOT_ON_SOURCE)
    if content_hash(ir) != source.content_hash:
        raise InvalidMigrationRequest(details=NOT_ON_SOURCE)
    if candidate.validation in {ValidationState.INVALID, ValidationState.UNSUPPORTED}:
        raise InvalidMigrationRequest(
            details={"field": "target.candidate_id", "reason": "candidate_not_valid"}
        )
    try:
        overlay = apply_candidate(ir, candidate)
    except InvalidCandidate:
        details = {"field": "target.candidate_id", "reason": "candidate_not_applicable"}
        raise InvalidMigrationRequest(details=details) from None
    target = TargetRef(
        TargetKind.CANDIDATE,
        source.architecture_id,
        source.revision_number,
        overlay.content_hash,
        analysis_id,
        candidate.id,
    )
    return target, overlay.architecture


def _property(field: str) -> str | None:
    head, _, rest = field.partition(".")
    return rest.split(".", 1)[0] if head == "configuration" and rest else None


class _Reading:
    """What one modified element's field changes touch, and what a person must interpret."""

    def __init__(self, change: ElementChange) -> None:
        self.change = change
        self.aspects: set[Aspect] = set()
        self.engines: set[str] = set()
        self.unsupported: list[str] = []
        self.manual: list[str] = []

    @property
    def stateful(self) -> bool:
        return self.change.element.value == "node" and self.change.kind in STATEFUL

    def read(self, field: FieldChange) -> None:
        category, element_id = field.category, self.change.element_id
        if category == "kind":
            self.aspects.add(Aspect.ROLE)
            self.unsupported.append(
                f"{element_id} changes its kind from {field.before} to {field.after}: no pattern plans a "
                "component changing its role."
            )
        elif category == "technology":
            self.aspects.add(Aspect.TECHNOLOGY)
        elif category == "endpoints":
            self.aspects.add(Aspect.ROUTING)
        elif category == "semantics":
            self.aspects.add(Aspect.PROTOCOL)
        elif category == "placement":
            self.aspects.add(Aspect.GROUPING)
            self.manual.append(
                f"{element_id} moves to another boundary: what that means operationally is not modeled."
            )
        elif category == "traceability":
            self.aspects.add(Aspect.TRACEABILITY)
        elif category in METADATA_CATEGORIES:
            self.aspects.add(Aspect.METADATA)
        else:  # resources and configuration
            self._configuration(field)

    def _configuration(self, field: FieldChange) -> None:
        element_id, prop = self.change.element_id, _property(field.field)
        if field.field == "configuration.unknown":
            self.aspects.add(Aspect.UNKNOWN_VALUES)
            self.manual.append(
                f"The values of {element_id} marked unknown change: what is known must be established before "
                "planning around it."
            )
            return
        if prop is None or field.field.startswith("configuration.extra."):
            self.aspects.add(Aspect.UNRECOGNIZED)
            self.manual.append(
                f"{element_id} changes a setting the architecture model does not define ({field.field}): no "
                "engine reads it."
            )
            return
        self.aspects.add(Aspect.RESOURCES if field.category == "resources" else Aspect.CONFIGURATION)
        self.engines |= {engine for engine, inputs in ENGINE_INPUTS.items() if prop in inputs}
        for group, aspect in GROUPS:
            if prop in group:
                self.aspects.add(aspect)
        if prop in PLACEMENT and self.stateful:
            self.manual.append(
                f"{element_id} holds data and changes its placement ({prop}): moving its data is not modeled."
            )

    def result(self) -> MigrationChange:
        change = self.change
        interpretation, reasons = Interpretation.SUPPORTED, None
        if self.unsupported:
            interpretation, reasons = UNSUPPORTED, self.unsupported + self.manual
        elif self.manual:
            interpretation, reasons = MANUAL, self.manual
        relevant = bool(self.aspects - DESCRIPTIVE)
        return MigrationChange(
            element=change.element.value,
            element_id=change.element_id,
            change=change.change,
            relevance=Relevance.MIGRATION if relevant else Relevance.METADATA,
            interpretation=interpretation,
            aspects=tuple(self.aspects),
            engines=tuple(self.engines),
            kind=change.kind,
            label=change.label,
            stateful=self.stateful,
            fields=change.fields,
            reason=" ".join(reasons) if reasons else None,
        )


def _element(change: ElementChange) -> MigrationChange:
    element, kind = change.element.value, change.kind
    if element in {"assumption", "decision"}:  # recorded reasoning, not what runs
        return MigrationChange(
            element,
            change.element_id,
            change.change,
            Relevance.METADATA,
            Interpretation.SUPPORTED,
            (Aspect.METADATA,),
            fields=change.fields,
        )
    if change.change is ChangeKind.MODIFIED:
        reading = _Reading(change)
        for field in change.fields:
            reading.read(field)
        return reading.result()
    added = change.change is ChangeKind.ADDED
    aspects = [Aspect.TOPOLOGY, Aspect.PROVISIONING if added else Aspect.DECOMMISSIONING]
    reason = None
    if element == "connection":
        aspects.append(Aspect.ROUTING)
    elif kind == BOUNDARY:
        aspects.append(Aspect.GROUPING)
        reason = (
            f"{change.element_id} is a boundary (a grouping, not a component): what adding or removing it "
            "means operationally is not modeled."
        )
    return MigrationChange(
        element=element,
        element_id=change.element_id,
        change=change.change,
        relevance=Relevance.MIGRATION,
        interpretation=MANUAL if reason else Interpretation.SUPPORTED,
        aspects=tuple(aspects),
        kind=kind,
        label=change.label,
        stateful=element == "node" and kind in STATEFUL,
        reason=reason,
    )


def _architecture(fields: tuple[FieldChange, ...]) -> Iterable[MigrationChange]:
    if not fields:
        return ()
    traced = any(f.category == "traceability" for f in fields)
    aspects = (Aspect.TRACEABILITY,) if traced else (Aspect.METADATA,)
    return (
        MigrationChange(
            "architecture", "", ChangeKind.MODIFIED, Relevance.METADATA, Interpretation.SUPPORTED, aspects,
            fields=fields,
        ),
    )  # fmt: skip


def _findings(changes: Iterable[MigrationChange]) -> list[PlanFinding]:
    found = []
    for change in changes:
        if change.interpretation is Interpretation.SUPPORTED or change.reason is None:
            continue
        unsupported = change.interpretation is UNSUPPORTED
        kind = FindingType.UNSUPPORTED_CHANGE if unsupported else FindingType.MANUAL_INTERPRETATION
        trace = Trace(TraceKind.CHANGE, change.ref)
        found.append(
            PlanFinding(kind, change.ref, change.reason, element_ids=(change.element_id,), traces=(trace,))
        )
    return found


def classify(architecture_diff: ArchitectureDiff, source: SourceRef, target: TargetRef) -> ChangeAnalysis:
    changes = [
        *(_element(c) for c in architecture_diff.changes()),
        *_architecture(architecture_diff.architecture),
    ]
    findings = _findings(changes)
    if not any(c.relevance is Relevance.MIGRATION for c in changes):
        message = "The source and the target do not differ in anything a migration changes."
        findings.append(PlanFinding(FindingType.NO_CHANGES, "no_changes", message))
    return ChangeAnalysis(source, target, architecture_diff.summary(), tuple(changes), tuple(findings))


def analyze(
    before: ArchitectureIR, after: ArchitectureIR, source: SourceRef, target: TargetRef
) -> ChangeAnalysis:
    """The classified changes from the exact source to the exact target (neither is modified)."""
    if content_hash(before) != source.content_hash or content_hash(after) != target.content_hash:
        raise InvalidMigrationRequest(details={"field": "target", "reason": "content_does_not_match"})
    return classify(diff(before, after), source, target)
