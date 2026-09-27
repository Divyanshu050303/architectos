"""What an evolution analysis reads from the other engines: each stored analysis, normalized to the
items that can become triggers, the requirement verdicts it reached, and a few analysis-level facts
— copied from the engine's own result, never recomputed.

A ``StoredAnalysis`` keeps the analysis id, the revision number and content hash it analyzed, and its
model version, so the trigger engine can tell current evidence (the baseline's exact content) from
stale evidence (another content: reported, never used).

- **Items**: a capacity scaling option (horizontal or vertical, with its current and required
  amounts), a resource over its target that no model scales (``scaling_unsupported``), or a finding
  of reliability, security, observability or validation (its stable id, type, element, the engine's
  recommendation, the requirement or dimension it concerns). Findings that only say something is
  not modeled are marked not ``evaluable``: they name what is missing, they never become triggers.
- **Checks**: each requirement verdict the engine reached (satisfied, violated, not verifiable).
- **Facts**: the capacity analysis's workload rate, the cost analysis's known monthly total and
  currency (and whether it is complete), each entry's modeled availability.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from core.domain.capacity.analyses import AnalysisReport
from core.domain.capacity.results import Bottleneck, BottleneckCondition
from core.domain.capacity.scenarios import ScalingOption
from core.domain.capacity.workload import WorkloadProfile
from core.domain.cost.reports import CostReport
from core.domain.engine_results import Evidence, FindingBasis
from core.domain.observability.reports import ObservabilityReport
from core.domain.observability.results import ObservabilityFinding
from core.domain.reliability.reports import ReliabilityReport
from core.domain.reliability.results import FindingType as ReliabilityType
from core.domain.reliability.results import ReliabilityFinding
from core.domain.requirements.value_objects import decimal_to_str
from core.domain.security.reports import SecurityReport
from core.domain.security.results import SecurityFinding
from core.domain.validation.results import Finding as ValidationFinding
from core.domain.validation.runs import ValidationRun

from .candidates import EvidenceRef
from .triggers import TriggerKind
from .values import EvidenceSource, EvidenceState

S = EvidenceSource
OVER = frozenset(
    {
        BottleneckCondition.EXCEEDS_CAPACITY,
        BottleneckCondition.AT_CAPACITY,
        BottleneckCondition.ABOVE_TARGET,
        BottleneckCondition.NO_CAPACITY,
    }
)
RELIABILITY_NOT_EVALUABLE = frozenset(
    {
        ReliabilityType.OBJECTIVE_NOT_EVALUABLE,
        ReliabilityType.AVAILABILITY_NOT_EVALUABLE,
        ReliabilityType.MISSING_RECOVERY_DATA,
        ReliabilityType.UNVERIFIED_RELIABILITY_DATA,
        ReliabilityType.UNMODELED_DEPENDENCY,
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    kind: TriggerKind
    code: str  # the finding type, or the resource
    element_id: str  # the node or connection it concerns
    item: str  # its stable id within the analysis
    facts: tuple[Evidence, ...] = ()
    message: str | None = None  # the engine's own recommendation
    evaluable: bool = True  # False: it only says something is not modeled
    missing: tuple[str, ...] = ()
    requirement_id: str | None = None
    dimension: str | None = None  # observability's signal dimension


@dataclass(frozen=True, slots=True)
class RequirementCheck:
    requirement_id: str
    verdict: str  # satisfied, violated, not_verifiable, not_applicable
    key: str
    missing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredAnalysis:
    source: EvidenceSource
    analysis_id: uuid.UUID
    revision_number: int
    content_hash: str
    model_version: str | None
    status: str
    items: tuple[EvidenceItem, ...] = ()
    checks: tuple[RequirementCheck, ...] = ()
    facts: tuple[Evidence, ...] = ()
    missing: tuple[str, ...] = ()  # inputs the analysis lacked (partial results)

    def fact(self, label: str) -> str | None:
        return next((f.value for f in self.facts if f.label == label), None)

    def ref(self, state: EvidenceState, item: str | None = None) -> EvidenceRef:
        return EvidenceRef(
            self.source,
            str(self.analysis_id),
            state,
            item,
            self.revision_number,
            self.content_hash,
            self.model_version,
        )


def _missing(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted({m for u in values for m in u.missing}))


# --- capacity -------------------------------------------------------------------------------------


def _scaling(option: ScalingOption) -> EvidenceItem:
    facts = (
        Evidence("scaling", option.kind.value),
        Evidence("current", decimal_to_str(option.current.value)),
        Evidence("required", decimal_to_str(option.required.value)),
        Evidence("unit", option.required.unit),
        Evidence("model", option.model_id),
        Evidence("basis", option.basis),
        *option.evidence,
    )
    item = f"scaling:{option.node_id}:{option.resource}:{option.kind.value}"
    return EvidenceItem(TriggerKind.SCALING_OPTION, option.resource, option.node_id, item, facts)


def from_capacity(report: AnalysisReport, bottlenecks: Iterable[Bottleneck]) -> StoredAnalysis:
    """Every scaling option, and every resource over its target that no option scales."""
    analysis = report.analysis
    items = [_scaling(o) for o in report.scaling]
    scaled = {(o.node_id, o.resource) for o in report.scaling}
    for b in bottlenecks:
        if b.condition in OVER and (b.node_id, b.resource) not in scaled:
            items.append(
                EvidenceItem(
                    TriggerKind.SCALING_UNSUPPORTED,
                    b.resource,
                    b.node_id,
                    f"bottleneck:{b.node_id}:{b.resource}",
                    b.evidence,
                    b.remediation,
                )
            )
    facts: list[Evidence] = []
    workload = report.inputs.get("workload")
    if workload:
        peak = WorkloadProfile.from_dict(workload).peak_rate
        if peak is not None:
            canonical = peak.canonical_quantity()
            facts += [
                Evidence("workload.peak_rate", decimal_to_str(canonical.value)),
                Evidence("workload.unit", canonical.unit),
            ]
    return StoredAnalysis(
        S.CAPACITY,
        analysis.id,
        analysis.revision_number,
        analysis.revision_content_hash,
        report.model_set.version if report.model_set else None,
        analysis.status,
        tuple(items),
        facts=tuple(facts),
        missing=_missing(report.unsupported),
    )


# --- cost -----------------------------------------------------------------------------------------


def from_cost(report: CostReport) -> StoredAnalysis:
    analysis = report.analysis
    facts = [Evidence("currency", report.currency)]
    if report.totals is not None:
        facts += [
            Evidence("monthly", decimal_to_str(report.totals.monthly.amount)),
            Evidence("complete", "true" if report.totals.complete else "false"),
        ]
    drivers = (report.summary or {}).get("drivers") or {}
    largest = drivers.get("largest_component")
    if largest:
        facts.append(Evidence("largest_component", str(largest.get("key"))))
    return StoredAnalysis(
        S.COST,
        analysis.id,
        analysis.revision_number,
        analysis.revision_content_hash,
        report.model_set.version if report.model_set else None,
        analysis.status,
        facts=tuple(facts),
        missing=_missing(report.unsupported),
    )


# --- findings -------------------------------------------------------------------------------------


def _element(node_ids: tuple[str, ...], connection_ids: tuple[str, ...]) -> str:
    """A connection finding concerns its connection; otherwise its first node."""
    return connection_ids[0] if connection_ids else node_ids[0] if node_ids else "system"


def from_reliability(report: ReliabilityReport, findings: Iterable[ReliabilityFinding]) -> StoredAnalysis:
    analysis = report.analysis
    items = tuple(
        EvidenceItem(
            TriggerKind.FINDING,
            f.type.value,
            _element(f.node_ids, f.connection_ids),
            f.id,
            f.evidence,
            f.recommendation,
            f.type not in RELIABILITY_NOT_EVALUABLE,
            f.missing,
        )
        for f in findings
    )
    checks = tuple(
        RequirementCheck(o.requirement_id, o.verdict.value, o.key, o.missing)
        for o in report.objectives
        if o.requirement_id
    )
    facts = tuple(
        Evidence(f"availability.{p.entry_id}", decimal_to_str(p.availability.quantity.value))
        for p in report.paths
        if p.availability.quantity is not None
    )
    return StoredAnalysis(
        S.RELIABILITY,
        analysis.id,
        analysis.revision_number,
        analysis.revision_content_hash,
        report.model_set.version if report.model_set else None,
        analysis.status,
        items,
        checks,
        facts,
        _missing(report.unsupported),
    )


def from_security(report: SecurityReport, findings: Iterable[SecurityFinding]) -> StoredAnalysis:
    analysis = report.analysis
    items = tuple(
        EvidenceItem(
            TriggerKind.FINDING,
            f.type.value,
            _element(f.node_ids, f.connection_ids),
            f.id,
            f.evidence,
            f.recommendation,
            f.basis is not FindingBasis.NOT_EVALUABLE,
            f.missing,
            f.requirement_id,
        )
        for f in findings
    )
    return StoredAnalysis(
        S.SECURITY,
        analysis.id,
        analysis.revision_number,
        analysis.revision_content_hash,
        report.analyzer_set.version if report.analyzer_set else None,
        analysis.status,
        items,
        tuple(
            RequirementCheck(c.requirement_id, c.verdict.value, c.key, c.missing)
            for c in report.checks
            if c.requirement_id
        ),
        missing=_missing(report.unsupported),
    )


def from_observability(
    report: ObservabilityReport, findings: Iterable[ObservabilityFinding]
) -> StoredAnalysis:
    analysis = report.analysis
    items = tuple(
        EvidenceItem(
            TriggerKind.FINDING,
            f.type.value,
            _element(f.node_ids, f.connection_ids),
            f.id,
            f.evidence,
            f.recommendation,
            f.basis is not FindingBasis.NOT_EVALUABLE,
            f.missing,
            f.requirement_id,
            f.dimension.value if f.dimension is not None else None,
        )
        for f in findings
    )
    return StoredAnalysis(
        S.OBSERVABILITY,
        analysis.id,
        analysis.revision_number,
        analysis.revision_content_hash,
        report.analyzer_set.version if report.analyzer_set else None,
        analysis.status,
        items,
        tuple(
            RequirementCheck(c.requirement_id, c.verdict.value, c.key, c.missing)
            for c in report.checks
            if c.requirement_id
        ),
        missing=_missing(report.unsupported),
    )


def from_validation(run: ValidationRun, findings: Iterable[ValidationFinding]) -> StoredAnalysis:
    items = tuple(
        EvidenceItem(
            TriggerKind.FINDING,
            f.code,
            f.entity_ids[0] if f.entity_ids else "system",
            f.id,
            tuple(Evidence(e.label, e.value) for e in f.evidence),
            f.remediation,
            requirement_id=f.requirement_id,
        )
        for f in findings
    )
    return StoredAnalysis(
        S.VALIDATION,
        run.id,
        run.revision_number,
        run.revision_content_hash,
        run.result.rule_set.version if run.result is not None else None,
        run.status.value,
        items,
    )
