"""What the changes touch and what the engines establish — deterministic, and only as far as it goes.

- **Requirements**: a requirement is related to a change only through the architecture's own
  traceability (an element's requirement references) or the validation engine's verdict on it — never
  by its wording. Its relation says how (``RequirementRelation``); a verdict differing between the
  states is ``potential`` impact, never "violated" unless the engine's verdict says so.
- **Decisions (ADRs)**: a decision whose related elements (the ADR's own list, or the IR's decision
  references) include a changed element *may require review* — never "invalid".
- **Engines**: each engine analyzes both states with the same inputs; findings are compared by their
  stable ids (introduced, resolved, unchanged), counts by severity, and named measures (capacity,
  cost) only when the engine produced both values. An engine without its inputs is ``not_evaluated``,
  with why — never estimated, never zero.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from .values import FindingState, ImpactStatus, RequirementRelation, check, code, count, items, text, texts

MAX_FINDINGS = 1000
MAX_MEASURES = 200
TRACED = frozenset({RequirementRelation.DIRECTLY_CHANGED, RequirementRelation.ELEMENT_CHANGED})


@dataclass(frozen=True, slots=True)
class RequirementImpact:
    requirement_id: uuid.UUID
    reference: str  # REQ-n
    version: int | None  # the version traced (None: undetermined)
    title: str
    statement: str
    relation: RequirementRelation
    element_ids: tuple[str, ...] = ()  # changed elements traced to it
    change_ids: tuple[str, ...] = ()
    base_verdict: str | None = None  # the validation engine's, in each state (None: not evaluated)
    target_verdict: str | None = None

    def __post_init__(self) -> None:
        potential = self.relation is RequirementRelation.POTENTIAL
        check(
            [
                None if isinstance(self.requirement_id, uuid.UUID) else "requirement.requirement_id",
                text(self.reference, "requirement.reference", 32),
                count(self.version, "requirement.version", required=False, minimum=1),
                text(self.title, "requirement.title", 200),
                text(self.statement, "requirement.statement", 4000),
                None if isinstance(self.relation, RequirementRelation) else "requirement.relation",
                texts(self.element_ids, "requirement.element_ids", 128),
                texts(self.change_ids, "requirement.change_ids", 64),
                code(self.base_verdict, "requirement.base_verdict", required=False),
                code(self.target_verdict, "requirement.target_verdict", required=False),
                "requirement.change_ids" if self.relation in TRACED and not self.change_ids else None,
                "requirement.verdicts" if potential and self.base_verdict == self.target_verdict else None,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": str(self.requirement_id),
            "reference": self.reference,
            "version": self.version,
            "title": self.title,
            "statement": self.statement,
            "relation": self.relation.value,
            "element_ids": list(self.element_ids),
            "change_ids": list(self.change_ids),
            "base_verdict": self.base_verdict,
            "target_verdict": self.target_verdict,
        }


@dataclass(frozen=True, slots=True)
class DecisionImpact:
    """An ADR whose related elements changed: it may require review. Nothing more is claimed."""

    decision_id: uuid.UUID
    reference: str  # ADR-n
    title: str
    status: str
    element_ids: tuple[str, ...]
    change_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.decision_id, uuid.UUID) else "decision.decision_id",
                text(self.reference, "decision.reference", 32),
                text(self.title, "decision.title", 200),
                code(self.status, "decision.status"),
                texts(self.element_ids, "decision.element_ids", 128),
                texts(self.change_ids, "decision.change_ids", 64),
                "decision.change_ids" if not self.change_ids else None,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": str(self.decision_id),
            "reference": self.reference,
            "title": self.title,
            "status": self.status,
            "element_ids": list(self.element_ids),
            "change_ids": list(self.change_ids),
            "note": "May require review: an element it concerns changed.",
        }


@dataclass(frozen=True, slots=True)
class FindingDelta:
    """One engine finding, by its stable id, introduced by or resolved in the target."""

    finding_id: str
    state: FindingState
    severity: str  # in the state where it is present
    title: str
    elements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                text(self.finding_id, "finding.finding_id", 256),
                None if isinstance(self.state, FindingState) else "finding.state",
                "finding.state" if self.state is FindingState.UNCHANGED else None,  # unchanged are counted
                code(self.severity, "finding.severity"),
                text(self.title, "finding.title", 500),
                texts(self.elements, "finding.elements", 128),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "state": self.state.value,
            "severity": self.severity,
            "title": self.title,
            "elements": list(self.elements),
        }


@dataclass(frozen=True, slots=True)
class MeasureDelta:
    """A value the engine calculated in both states (never one side alone, never a derived score)."""

    name: str  # e.g. "monthly_cost", "max_utilization:orders-api"
    before: str  # canonical decimal, as the engine produced it
    after: str
    unit: str

    def __post_init__(self) -> None:
        check(
            [
                text(self.name, "measure.name", 200),
                text(self.before, "measure.before", 64),
                text(self.after, "measure.after", 64),
                text(self.unit, "measure.unit", 32),
            ]
        )

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "before": self.before, "after": self.after, "unit": self.unit}


@dataclass(frozen=True, slots=True)
class EngineImpact:
    engine: str  # validation, reliability, security, observability, capacity, cost
    status: ImpactStatus
    versions: dict[str, Any] = field(default_factory=dict)
    findings: tuple[FindingDelta, ...] = ()  # introduced and resolved (unchanged are counted)
    unchanged: int = 0
    base_summary: dict[str, Any] = field(default_factory=dict)  # the engine's own counts per state
    target_summary: dict[str, Any] = field(default_factory=dict)
    measures: tuple[MeasureDelta, ...] = ()
    limitations: tuple[str, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        evaluated = self.status is ImpactStatus.EVALUATED
        produced = bool(self.findings or self.measures or self.unchanged)
        check(
            [
                code(self.engine, "impact.engine"),
                None if isinstance(self.status, ImpactStatus) else "impact.status",
                items(self.findings, FindingDelta, "impact.findings", MAX_FINDINGS),
                count(self.unchanged, "impact.unchanged"),
                items(self.measures, MeasureDelta, "impact.measures", MAX_MEASURES),
                texts(self.limitations, "impact.limitations"),
                code(self.error, "impact.error", required=False),
                "impact.error" if (self.status is ImpactStatus.FAILED) != (self.error is not None) else None,
                "impact.findings" if not evaluated and produced else None,
                "impact.limitations"
                if self.status is ImpactStatus.NOT_EVALUATED and not self.limitations
                else None,
            ]
        )

    def of_state(self, state: FindingState) -> tuple[FindingDelta, ...]:
        return tuple(f for f in self.findings if f.state is state)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "status": self.status.value,
            "versions": dict(self.versions),
            "findings": [f.to_dict() for f in self.findings],
            "unchanged": self.unchanged,
            "base_summary": dict(self.base_summary),
            "target_summary": dict(self.target_summary),
            "measures": [m.to_dict() for m in self.measures],
            "limitations": list(self.limitations),
            "error": self.error,
        }
