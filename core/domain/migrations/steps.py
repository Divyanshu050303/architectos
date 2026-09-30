"""The parts of a migration plan: steps, risks, verification checkpoints, rollback considerations,
data migrations and compatibility checks — each with a stable id and the traces it rests on.

- A **trace** says what an item rests on: an architecture change, a requirement, an explicit
  assumption, a migration pattern (with its version), a stated constraint, or a stored analysis.
  Every generated item has at least one: nothing appears without a reason.
- **Ids** are derived from a key the generating rule gives (e.g. ``provision:cache``): the same
  plan inputs always give the same ids. A step's dependencies are step ids.
- A **step** is planned, never executed: it states its preconditions, required inputs, expected
  outcome and completion criteria in words, never infrastructure commands. Parallel execution is
  never assumed: a step is parallelizable only when a rule says so explicitly.
- **Unknown stays unknown**: downtime, reversibility, compatibility and a checkpoint's status each
  have an explicit unknown; a planning-time checkpoint is never proof of runtime success.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.domain.evolution.candidates import EvidenceRef
from core.domain.evolution.values import EvidenceState

from .values import (
    MAX_REFERENCE,
    MAX_TITLE,
    CheckpointBasis,
    CheckpointStatus,
    CompatibilityStatus,
    DowntimeStatus,
    Reversibility,
    RiskCategory,
    RiskStatus,
    StepType,
    TraceKind,
    check,
    digest,
    items,
    references,
    text,
    texts,
)

MAX_KEY = 128
UNEVALUABLE = "unevaluable"


def _key(value: object, name: str = "key") -> str | None:
    return text(value, name, MAX_KEY)


def _traced(traces: object) -> str | None:
    """Every generated item names what it rests on."""
    return None if isinstance(traces, tuple) and traces else "traces"


def step_id(key: str) -> str:
    return digest("stp", key)


@dataclass(frozen=True, slots=True)
class Trace:
    kind: TraceKind
    reference: str  # e.g. "node:db:modified", "rolling@1", a requirement id, an assumption key
    detail: str | None = None

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.kind, TraceKind) else "trace.kind",
                text(self.reference, "trace.reference", MAX_REFERENCE),
                text(self.detail, "trace.detail", required=False),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "reference": self.reference, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class MigrationStep:
    key: str  # the generating rule's key, unique in the plan
    type: StepType
    title: str
    outcome: str  # the expected outcome, in words
    traces: tuple[Trace, ...]
    completion: tuple[str, ...]  # completion criteria
    description: str | None = None
    element_ids: tuple[str, ...] = ()  # the architecture elements it concerns
    preconditions: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()  # what must be provided before it can be carried out
    depends_on: tuple[str, ...] = ()  # step ids
    parallelizable: bool = False  # only when a rule states it explicitly
    downtime: DowntimeStatus = DowntimeStatus.UNKNOWN
    downtime_note: str | None = None
    traffic: str | None = None  # its traffic-routing implications, in words
    availability: tuple[str, ...] = ()  # availability and recovery considerations
    data_impact: str | None = None
    reversibility: Reversibility = Reversibility.UNKNOWN
    manual_verification: bool = False  # a person must verify before the plan proceeds past it
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                _key(self.key),
                None if isinstance(self.type, StepType) else "type",
                text(self.title, "title", MAX_TITLE),
                text(self.outcome, "outcome"),
                items(self.traces, Trace, "traces"),
                _traced(self.traces),
                texts(self.completion, "completion"),
                None if self.completion else "completion",
                text(self.description, "description", required=False),
                references(self.element_ids, "element_ids"),
                texts(self.preconditions, "preconditions"),
                texts(self.inputs, "inputs"),
                references(self.depends_on, "depends_on"),
                "depends_on" if isinstance(self.key, str) and step_id(self.key) in self.depends_on else None,
                None if isinstance(self.parallelizable, bool) else "parallelizable",
                None if isinstance(self.downtime, DowntimeStatus) else "downtime",
                text(self.downtime_note, "downtime_note", required=False),
                text(self.traffic, "traffic", required=False),
                texts(self.availability, "availability"),
                text(self.data_impact, "data_impact", required=False),
                None if isinstance(self.reversibility, Reversibility) else "reversibility",
                None if isinstance(self.manual_verification, bool) else "manual_verification",
                texts(self.assumptions, "assumptions"),
                # a downtime that may happen says under which conditions
                "downtime_note"
                if self.downtime is DowntimeStatus.POTENTIAL_DOWNTIME and not self.downtime_note
                else None,
            ]
        )
        object.__setattr__(self, "element_ids", tuple(sorted(set(self.element_ids))))
        object.__setattr__(self, "depends_on", tuple(sorted(set(self.depends_on))))

    @property
    def id(self) -> str:
        return step_id(self.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "type": self.type.value,
            "title": self.title,
            "description": self.description,
            "element_ids": list(self.element_ids),
            "preconditions": list(self.preconditions),
            "inputs": list(self.inputs),
            "outcome": self.outcome,
            "completion": list(self.completion),
            "depends_on": list(self.depends_on),
            "parallelizable": self.parallelizable,
            "downtime": self.downtime.value,
            "downtime_note": self.downtime_note,
            "traffic": self.traffic,
            "availability": list(self.availability),
            "data_impact": self.data_impact,
            "reversibility": self.reversibility.value,
            "manual_verification": self.manual_verification,
            "assumptions": list(self.assumptions),
            "traces": [t.to_dict() for t in self.traces],
        }


@dataclass(frozen=True, slots=True)
class Risk:
    """A migration risk, from evidence or an explicit assumption — no probability, no score."""

    key: str
    category: RiskCategory
    status: RiskStatus
    description: str
    impact: str
    traces: tuple[Trace, ...]
    element_ids: tuple[str, ...] = ()
    step_ids: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()  # when a potential risk materializes
    mitigation: str | None = None  # a mitigation or a review action

    def __post_init__(self) -> None:
        check(
            [
                _key(self.key),
                None if isinstance(self.category, RiskCategory) else "category",
                None if isinstance(self.status, RiskStatus) else "status",
                text(self.description, "description"),
                text(self.impact, "impact"),
                items(self.traces, Trace, "traces"),
                _traced(self.traces),
                references(self.element_ids, "element_ids"),
                references(self.step_ids, "step_ids"),
                texts(self.preconditions, "preconditions"),
                text(self.mitigation, "mitigation", required=False),
                "preconditions" if self.status is RiskStatus.POTENTIAL and not self.preconditions else None,
            ]
        )
        object.__setattr__(self, "element_ids", tuple(sorted(set(self.element_ids))))
        object.__setattr__(self, "step_ids", tuple(sorted(set(self.step_ids))))

    @property
    def id(self) -> str:
        return digest("rsk", self.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "category": self.category.value,
            "status": self.status.value,
            "description": self.description,
            "impact": self.impact,
            "element_ids": list(self.element_ids),
            "step_ids": list(self.step_ids),
            "preconditions": list(self.preconditions),
            "mitigation": self.mitigation,
            "traces": [t.to_dict() for t in self.traces],
        }


EVALUATED = frozenset({CheckpointStatus.PASS, CheckpointStatus.FAIL, CheckpointStatus.WARNING})
AWAITING = frozenset({CheckpointStatus.MANUAL_VERIFICATION_REQUIRED, CheckpointStatus.NOT_RUN})


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A condition to verify before proceeding. A planning-time status is a model's statement (with
    its evidence) or a request for manual verification — never proof of runtime success."""

    key: str
    subject: str  # what is verified
    expected: str  # the expected condition
    status: CheckpointStatus
    basis: CheckpointBasis
    traces: tuple[Trace, ...]
    blocking: bool = True  # the plan must not proceed past it while it does not pass
    step_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    actual: str | None = None  # what the evidence states, when it states something

    def __post_init__(self) -> None:
        evidence = self.evidence if isinstance(self.evidence, tuple) else ()
        current = any(isinstance(e, EvidenceRef) and e.state is EvidenceState.CURRENT for e in evidence)
        evaluated = self.status in EVALUATED
        check(
            [
                _key(self.key),
                text(self.subject, "subject"),
                text(self.expected, "expected"),
                None if isinstance(self.status, CheckpointStatus) else "status",
                None if isinstance(self.basis, CheckpointBasis) else "basis",
                items(self.traces, Trace, "traces"),
                _traced(self.traces),
                None if isinstance(self.blocking, bool) else "blocking",
                references(self.step_ids, "step_ids"),
                items(self.evidence, EvidenceRef, "evidence"),
                text(self.actual, "actual", required=False),
                # an evaluated status rests on current evidence, modeled — never observed at planning time
                "evidence" if evaluated and not current else None,
                "basis" if evaluated and self.basis is not CheckpointBasis.MODELED else None,
                "status" if self.basis is CheckpointBasis.MANUAL and self.status not in AWAITING else None,
            ]
        )
        object.__setattr__(self, "step_ids", tuple(sorted(set(self.step_ids))))
        object.__setattr__(self, "evidence", tuple(sorted(set(evidence), key=lambda e: e.key)))

    @property
    def id(self) -> str:
        return digest("chk", self.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "subject": self.subject,
            "expected": self.expected,
            "status": self.status.value,
            "basis": self.basis.value,
            "blocking": self.blocking,
            "actual": self.actual,
            "step_ids": list(self.step_ids),
            "evidence": [e.to_dict() for e in self.evidence],
            "traces": [t.to_dict() for t in self.traces],
        }


RECOVERABLE = frozenset({Reversibility.REVERSIBLE, Reversibility.CONDITIONALLY_REVERSIBLE})
UNRECOVERABLE = frozenset({Reversibility.IRREVERSIBLE, Reversibility.UNKNOWN})


@dataclass(frozen=True, slots=True)
class RollbackConsideration:
    """How a step could be recovered from — a proposal for review, never an executable instruction,
    and never a generic 'undo' that implies safety."""

    step_id: str
    reversibility: Reversibility
    traces: tuple[Trace, ...]
    triggers: tuple[str, ...] = ()  # the conditions that would call for it
    action: str | None = None  # the recovery action, in words
    retained: tuple[str, ...] = ()  # resources or data that must be kept for it to remain possible
    preconditions: tuple[str, ...] = ()
    consistency: str | None = None  # the data-consistency implications
    verification: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        reversibility = self.reversibility
        conditional = reversibility is Reversibility.CONDITIONALLY_REVERSIBLE
        check(
            [
                text(self.step_id, "step_id", MAX_REFERENCE),
                None if isinstance(reversibility, Reversibility) else "reversibility",
                items(self.traces, Trace, "traces"),
                _traced(self.traces),
                texts(self.triggers, "triggers"),
                text(self.action, "action", required=False),
                texts(self.retained, "retained"),
                texts(self.preconditions, "preconditions"),
                text(self.consistency, "consistency", required=False),
                texts(self.verification, "verification"),
                texts(self.limitations, "limitations"),
                # a recoverable step says how; a conditional one under what; the others why not
                "action" if reversibility in RECOVERABLE and not self.action else None,
                "preconditions" if conditional and not self.preconditions else None,
                "limitations" if reversibility in UNRECOVERABLE and not self.limitations else None,
            ]
        )

    @property
    def id(self) -> str:
        return digest("rbk", self.step_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "step_id": self.step_id,
            "reversibility": self.reversibility.value,
            "triggers": list(self.triggers),
            "action": self.action,
            "retained": list(self.retained),
            "preconditions": list(self.preconditions),
            "consistency": self.consistency,
            "verification": list(self.verification),
            "limitations": list(self.limitations),
            "traces": [t.to_dict() for t in self.traces],
        }


@dataclass(frozen=True, slots=True)
class DataMigration:
    """What moving (or removing) a stateful component's data involves. Volumes, throughput and
    durations are never stated here: the architecture does not model them, so they are among
    ``missing`` and the duration is ``unevaluable``."""

    key: str
    traces: tuple[Trace, ...]
    source_element_id: str | None = None
    destination_element_id: str | None = None
    scope: str | None = None  # what data, when stated
    method: str | None = None  # e.g. replication then cutover, when a pattern supports it
    backfill: str | None = None  # the initial copy of existing data
    replication: str | None = None  # the replication or change-capture requirements
    cutover: tuple[str, ...] = ()  # the prerequisites of the cutover
    step_ids: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()  # how consistency is verified
    retention: tuple[str, ...] = ()  # retention requirements
    rollback: str | None = None  # the rollback implications
    data_loss: str | None = None  # potential data-loss considerations
    missing: tuple[str, ...] = ()  # what must be stated before it can be planned with confidence

    def __post_init__(self) -> None:
        check(
            [
                _key(self.key),
                items(self.traces, Trace, "traces"),
                _traced(self.traces),
                text(self.source_element_id, "source_element_id", MAX_REFERENCE, required=False),
                text(self.destination_element_id, "destination_element_id", MAX_REFERENCE, required=False),
                None if self.source_element_id or self.destination_element_id else "source_element_id",
                text(self.scope, "scope", required=False),
                text(self.method, "method", required=False),
                text(self.backfill, "backfill", required=False),
                text(self.replication, "replication", required=False),
                texts(self.cutover, "cutover"),
                references(self.step_ids, "step_ids"),
                texts(self.verification, "verification"),
                texts(self.retention, "retention"),
                text(self.rollback, "rollback", required=False),
                text(self.data_loss, "data_loss", required=False),
                texts(self.missing, "missing"),
            ]
        )
        object.__setattr__(self, "step_ids", tuple(sorted(set(self.step_ids))))

    @property
    def id(self) -> str:
        return digest("dat", self.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "source_element_id": self.source_element_id,
            "destination_element_id": self.destination_element_id,
            "scope": self.scope,
            "method": self.method,
            "backfill": self.backfill,
            "replication": self.replication,
            "cutover": list(self.cutover),
            "duration": UNEVALUABLE,  # volume and throughput are not modeled
            "step_ids": list(self.step_ids),
            "verification": list(self.verification),
            "retention": list(self.retention),
            "rollback": self.rollback,
            "data_loss": self.data_loss,
            "missing": list(self.missing),
            "traces": [t.to_dict() for t in self.traces],
        }


class CompatibilityAspect(StrEnum):
    DATA_MODEL = "data_model"
    APPLICATION = "application"
    PROTOCOL = "protocol"
    SCHEMA = "schema"
    VERSION = "version"
    AUTHENTICATION = "authentication"
    CLIENT_CHANGES = "client_changes"
    ROLLBACK = "rollback"


@dataclass(frozen=True, slots=True)
class CompatibilityCheck:
    """A compatibility question. ``verified`` only with machine-checkable evidence (a stored
    analysis), never from the architecture's declarations alone."""

    key: str
    aspect: CompatibilityAspect
    status: CompatibilityStatus
    question: str
    traces: tuple[Trace, ...]
    element_ids: tuple[str, ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        traces = self.traces if isinstance(self.traces, tuple) else ()
        evidenced = any(isinstance(t, Trace) and t.kind is TraceKind.EVIDENCE for t in traces)
        check(
            [
                _key(self.key),
                None if isinstance(self.aspect, CompatibilityAspect) else "aspect",
                None if isinstance(self.status, CompatibilityStatus) else "status",
                text(self.question, "question"),
                items(self.traces, Trace, "traces"),
                _traced(self.traces),
                references(self.element_ids, "element_ids"),
                text(self.note, "note", required=False),
                "traces" if self.status is CompatibilityStatus.VERIFIED and not evidenced else None,
            ]
        )
        object.__setattr__(self, "element_ids", tuple(sorted(set(self.element_ids))))

    @property
    def id(self) -> str:
        return digest("cmp", self.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "aspect": self.aspect.value,
            "status": self.status.value,
            "question": self.question,
            "element_ids": list(self.element_ids),
            "note": self.note,
            "traces": [t.to_dict() for t in self.traces],
        }
