"""What the migration planning engine produces for one exact source and one exact target: the
proposal — steps, risks, checkpoints, rollback considerations, data migrations, compatibility
checks and findings — with the rule and model versions that produced it.

- The **source** is an exact revision (architecture, revision number, content hash). The **target**
  is either a later exact revision of the same architecture, or an evolution candidate (its analysis
  and candidate id) applied to its exact baseline, which must be the source revision. Neither
  carries topology: the architecture is read from its revisions, never duplicated here.
- The proposal is **internally consistent**: every id it refers to (a risk's steps, a checkpoint's
  steps, a rollback's step, a data migration's steps) exists in it, and ids are unique. A step's
  dependency on a step that does not exist, or a cycle, is not refused here: the dependency analysis
  reports it as a finding.
- The **sequence** is the order the steps would be carried out in: numbered stages, each one step,
  or several steps together only when every one of them is explicitly parallelizable. It covers
  every step exactly once, and is empty when the dependencies are invalid or cyclic.
- Its **status** follows from its findings: ``needs_information`` when something it needs is
  missing or unsupported, ``draft`` otherwise. It is never approved by the engine.
"""

import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.domain.evolution.candidates import CANDIDATE_ID, EvidenceRef

from .steps import (
    Checkpoint,
    CompatibilityCheck,
    DataMigration,
    MigrationStep,
    Risk,
    RollbackConsideration,
    Trace,
)
from .values import (
    FINGERPRINT,
    DowntimeStatus,
    FindingType,
    PlanStatus,
    Reversibility,
    TargetKind,
    check,
    code,
    digest,
    fingerprint,
    items,
    references,
    text,
    texts,
)

MAX_STEPS = 500
MAX_FINDINGS = 1000
# Findings that keep a plan from being reviewable as it stands.
BLOCKING_FINDINGS = frozenset(
    {
        FindingType.MISSING_INFORMATION,
        FindingType.UNSUPPORTED_CHANGE,
        FindingType.MANUAL_INTERPRETATION,
        FindingType.INVALID_DEPENDENCY,
        FindingType.DEPENDENCY_CYCLE,
        FindingType.MISSING_PREREQUISITE,
        FindingType.CONSTRAINT_CONFLICT,
        FindingType.STALE_EVIDENCE,
    }
)


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _hash(value: object, name: str) -> str | None:
    return None if isinstance(value, str) and FINGERPRINT.fullmatch(value) else name


@dataclass(frozen=True, slots=True)
class SourceRef:
    architecture_id: uuid.UUID
    revision_number: int
    content_hash: str

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.architecture_id, uuid.UUID) else "source.architecture_id",
                None if _count(self.revision_number) else "source.revision_number",
                _hash(self.content_hash, "source.content_hash"),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture_id": str(self.architecture_id),
            "revision_number": self.revision_number,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class TargetRef:
    """A later revision (``revision_number`` is the target's), or a candidate on its baseline
    (``revision_number`` is the baseline's; ``content_hash`` is the overlay's)."""

    kind: TargetKind
    architecture_id: uuid.UUID
    revision_number: int
    content_hash: str
    analysis_id: uuid.UUID | None = None  # the evolution analysis, for a candidate
    candidate_id: str | None = None

    def __post_init__(self) -> None:
        candidate = self.kind is TargetKind.CANDIDATE
        analysis_ok = (self.analysis_id is not None) == candidate and (
            self.analysis_id is None or isinstance(self.analysis_id, uuid.UUID)
        )
        candidate_ok = (self.candidate_id is not None) == candidate and (
            self.candidate_id is None or CANDIDATE_ID.fullmatch(str(self.candidate_id)) is not None
        )
        check(
            [
                None if isinstance(self.kind, TargetKind) else "target.kind",
                None if isinstance(self.architecture_id, uuid.UUID) else "target.architecture_id",
                None if _count(self.revision_number) else "target.revision_number",
                _hash(self.content_hash, "target.content_hash"),
                None if analysis_ok else "target.analysis_id",
                None if candidate_ok else "target.candidate_id",
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "architecture_id": str(self.architecture_id),
            "revision_number": self.revision_number,
            "content_hash": self.content_hash,
            "analysis_id": str(self.analysis_id) if self.analysis_id else None,
            "candidate_id": self.candidate_id,
        }


def pair_problems(source: SourceRef, target: TargetRef) -> list[str | None]:
    """The target is the same architecture's: a later revision, or a candidate on the source."""
    same = target.architecture_id == source.architecture_id
    if target.kind is TargetKind.REVISION:
        placed = target.revision_number > source.revision_number
    else:
        placed = target.revision_number == source.revision_number
    return [None if same else "target.architecture_id", None if placed else "target.revision_number"]


@dataclass(frozen=True, slots=True)
class PlanFinding:
    type: FindingType
    key: str  # unique for its type within the plan
    message: str
    element_ids: tuple[str, ...] = ()
    step_ids: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()  # what would resolve it
    traces: tuple[Trace, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.type, FindingType) else "finding.type",
                text(self.key, "finding.key", 128),
                text(self.message, "finding.message"),
                references(self.element_ids, "finding.element_ids"),
                references(self.step_ids, "finding.step_ids"),
                texts(self.missing, "finding.missing"),
                items(self.traces, Trace, "finding.traces"),
            ]
        )
        object.__setattr__(self, "element_ids", tuple(sorted(set(self.element_ids))))
        object.__setattr__(self, "step_ids", tuple(sorted(set(self.step_ids))))

    @property
    def id(self) -> str:
        return digest("mfd", self.type.value, self.key)

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.type.value, self.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "key": self.key,
            "message": self.message,
            "element_ids": list(self.element_ids),
            "step_ids": list(self.step_ids),
            "missing": list(self.missing),
            "traces": [t.to_dict() for t in self.traces],
        }


@dataclass(frozen=True, slots=True)
class StrategyOption:
    """A migration strategy considered for this plan: whether it applies, whether its prerequisites
    are modeled (and what is missing), and its trade-offs — side by side with the others, never
    scored or ranked."""

    pattern: str  # the pattern's ref, e.g. "rolling@1"
    name: str
    applies: bool  # the changes include what it is for
    supported: bool  # it applies and every prerequisite is modeled
    subjects: tuple[str, ...] = ()  # the elements it would cover
    missing: tuple[str, ...] = ()  # what must be declared for it to be supported
    tradeoffs: tuple[str, ...] = ()
    downtime: DowntimeStatus = DowntimeStatus.UNKNOWN
    reversibility: Reversibility = Reversibility.UNKNOWN

    def __post_init__(self) -> None:
        check(
            [
                text(self.pattern, "strategy.pattern", 64),
                text(self.name, "strategy.name", 100),
                None if isinstance(self.applies, bool) else "strategy.applies",
                None if isinstance(self.supported, bool) else "strategy.supported",
                "strategy.supported" if self.supported and (not self.applies or self.missing) else None,
                references(self.subjects, "strategy.subjects"),
                texts(self.missing, "strategy.missing"),
                texts(self.tradeoffs, "strategy.tradeoffs"),
                None if isinstance(self.downtime, DowntimeStatus) else "strategy.downtime",
                None if isinstance(self.reversibility, Reversibility) else "strategy.reversibility",
            ]
        )
        object.__setattr__(self, "subjects", tuple(sorted(set(self.subjects))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "name": self.name,
            "applies": self.applies,
            "supported": self.supported,
            "subjects": list(self.subjects),
            "missing": list(self.missing),
            "tradeoffs": list(self.tradeoffs),
            "downtime": self.downtime.value,
            "reversibility": self.reversibility.value,
        }


@dataclass(frozen=True, slots=True)
class Stage:
    """One position in the plan's sequence: its steps are carried out together only when
    ``parallel`` — which requires every one of them to be explicitly parallelizable."""

    number: int  # from 1
    step_ids: tuple[str, ...]
    parallel: bool = False

    def __post_init__(self) -> None:
        check(
            [
                None if _count(self.number) else "stage.number",
                references(self.step_ids, "stage.step_ids") if self.step_ids else "stage.step_ids",
                None if isinstance(self.parallel, bool) else "stage.parallel",
                "stage.parallel" if self.parallel and len(self.step_ids) < 2 else None,
                "stage.step_ids" if not self.parallel and len(self.step_ids) != 1 else None,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {"number": self.number, "step_ids": list(self.step_ids), "parallel": self.parallel}


def _findings(values: object) -> str | None:
    ok = (
        isinstance(values, tuple)
        and len(values) <= MAX_FINDINGS
        and all(isinstance(f, PlanFinding) for f in values)
    )
    return None if ok else "findings"


def _models(values: object) -> str | None:
    ok = isinstance(values, Mapping) and all(isinstance(k, str) and _count(v) for k, v in values.items())
    return None if ok else "models"


@dataclass(frozen=True, slots=True)
class MigrationProposal:
    source: SourceRef
    target: TargetRef
    steps: tuple[MigrationStep, ...] = ()
    risks: tuple[Risk, ...] = ()
    checkpoints: tuple[Checkpoint, ...] = ()
    rollbacks: tuple[RollbackConsideration, ...] = ()
    data_migrations: tuple[DataMigration, ...] = ()
    compatibility: tuple[CompatibilityCheck, ...] = ()
    findings: tuple[PlanFinding, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()  # every stored analysis considered, with its state
    assumptions: tuple[str, ...] = ()  # the request's, recorded — never computed with
    strategy: str | None = None  # the strategy the steps follow: one of the supported alternatives
    models: Mapping[str, int] = field(default_factory=dict)  # rule and model versions used
    diff_summary: str | None = None  # the architecture diff's summary, for display
    alternatives: tuple[StrategyOption, ...] = ()  # the strategies considered, side by side
    sequence: tuple[Stage, ...] = ()  # the steps in order; empty when the dependencies are invalid

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.source, SourceRef) else "source",
                None if isinstance(self.target, TargetRef) else "target",
                items(self.steps, MigrationStep, "steps") if len(self.steps) <= MAX_STEPS else "steps",
                items(self.risks, Risk, "risks"),
                items(self.checkpoints, Checkpoint, "checkpoints"),
                items(self.rollbacks, RollbackConsideration, "rollbacks"),
                items(self.data_migrations, DataMigration, "data_migrations"),
                items(self.compatibility, CompatibilityCheck, "compatibility"),
                _findings(self.findings),
                items(self.evidence, EvidenceRef, "evidence"),
                texts(self.assumptions, "assumptions"),
                code(self.strategy, "strategy") if self.strategy is not None else None,
                _models(self.models),
                text(self.diff_summary, "diff_summary", required=False),
                items(self.alternatives, StrategyOption, "alternatives"),
                items(self.sequence, Stage, "sequence"),
                "strategy"
                if self.strategy is not None
                and not any(
                    o.supported and o.pattern.split("@")[0] == self.strategy for o in self.alternatives
                )
                else None,
            ]
        )
        check(pair_problems(self.source, self.target))
        check(self._consistency())
        object.__setattr__(self, "steps", tuple(sorted(self.steps, key=lambda s: s.id)))
        for name in ("risks", "checkpoints", "rollbacks", "data_migrations", "compatibility"):
            object.__setattr__(self, name, tuple(sorted(getattr(self, name), key=lambda x: x.id)))
        object.__setattr__(self, "findings", tuple(sorted(set(self.findings), key=lambda f: f.sort_key)))
        object.__setattr__(self, "evidence", tuple(sorted(set(self.evidence), key=lambda e: e.key)))
        object.__setattr__(self, "models", dict(sorted(self.models.items())))
        object.__setattr__(self, "alternatives", tuple(sorted(self.alternatives, key=lambda o: o.pattern)))

    def _consistency(self) -> list[str | None]:
        steps = [s.id for s in self.steps]
        known = set(steps)
        referred = {
            "risks": {i for r in self.risks for i in r.step_ids},
            "checkpoints": {i for c in self.checkpoints for i in c.step_ids},
            "rollbacks": {r.step_id for r in self.rollbacks},
            "data_migrations": {i for d in self.data_migrations for i in d.step_ids},
        }
        unique = {
            "steps": steps,
            "risks": [r.id for r in self.risks],
            "checkpoints": [c.id for c in self.checkpoints],
            "rollbacks": [r.id for r in self.rollbacks],
            "data_migrations": [d.id for d in self.data_migrations],
            "compatibility": [c.id for c in self.compatibility],
            "findings": [f.id for f in set(self.findings)],
        }
        return [
            *(f"{name}.step_ids" for name, ids in referred.items() if ids - known),
            *(name for name, ids in unique.items() if len(ids) != len(set(ids))),
            self._sequence_problem(),
        ]

    def _sequence_problem(self) -> str | None:
        """An empty sequence, or every step exactly once in numbered stages, parallel only when
        every step of the stage is explicitly parallelizable."""
        if not self.sequence:
            return None
        parallelizable = {s.id: s.parallelizable for s in self.steps}
        placed = [i for stage in self.sequence for i in stage.step_ids]
        ok = (
            [s.number for s in self.sequence] == list(range(1, len(self.sequence) + 1))
            and sorted(placed) == sorted(parallelizable)
            and all(
                all(parallelizable[i] for i in stage.step_ids) for stage in self.sequence if stage.parallel
            )
        )
        return None if ok else "sequence"

    @property
    def status(self) -> PlanStatus:
        """``needs_information`` while a finding keeps the plan from being reviewable as it stands."""
        blocked = any(f.type in BLOCKING_FINDINGS for f in self.findings) or not self.steps
        return PlanStatus.NEEDS_INFORMATION if blocked else PlanStatus.DRAFT

    def summary(self) -> dict[str, Any]:
        """Counts only — no score."""
        findings = Counter(f.type.value for f in self.findings)
        return {
            "steps": len(self.steps),
            "steps_by_type": dict(sorted(Counter(s.type.value for s in self.steps).items())),
            "risks": len(self.risks),
            "checkpoints": len(self.checkpoints),
            "blocking_checkpoints": sum(1 for c in self.checkpoints if c.blocking),
            "manual_verification_steps": sum(1 for s in self.steps if s.manual_verification),
            "stages": len(self.sequence),
            "downtime": dict(sorted(Counter(s.downtime.value for s in self.steps).items())),
            "data_migrations": len(self.data_migrations),
            "compatibility": dict(sorted(Counter(c.status.value for c in self.compatibility).items())),
            "parallel_stages": sum(1 for s in self.sequence if s.parallel),
            "findings": {t.value: findings.get(t.value, 0) for t in FindingType},
        }

    def _content(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "strategy": self.strategy,
            "steps": [s.to_dict() for s in self.steps],
            "risks": [r.to_dict() for r in self.risks],
            "checkpoints": [c.to_dict() for c in self.checkpoints],
            "rollbacks": [r.to_dict() for r in self.rollbacks],
            "data_migrations": [d.to_dict() for d in self.data_migrations],
            "compatibility": [c.to_dict() for c in self.compatibility],
            "findings": [f.to_dict() for f in self.findings],
            "evidence": [e.to_dict() for e in self.evidence],
            "assumptions": list(self.assumptions),
            "models": dict(self.models),
            "diff_summary": self.diff_summary,
            "alternatives": [o.to_dict() for o in self.alternatives],
            "sequence": [s.to_dict() for s in self.sequence],
        }

    @property
    def fingerprint(self) -> str:
        return fingerprint(self._content())

    def to_dict(self) -> dict[str, Any]:
        content = self._content()
        return content | {
            "status": self.status.value,
            "summary": self.summary(),
            "fingerprint": self.fingerprint,
        }
