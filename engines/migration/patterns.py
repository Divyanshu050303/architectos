"""Migration patterns: the deterministic, versioned registry of how changes can be carried out.

A **pattern** declares what it is (``PatternMeta``): its id and version, whether it is a per-change
**template** (reconfigure, provision, decommission, reroute) or a plan-wide **strategy** (in-place,
rolling, blue-green, replication then cutover, …), the changes it applies to, the evidence it
requires, its preconditions, the component capabilities it relies on, the step types it produces
and their dependency rules, its known risks, its trade-offs, its downtime and reversibility when its
prerequisites hold, its data-migration requirements, the validation it needs, and what it does not
support.

A pattern **assesses** a planning context: whether it applies (the changes include what it is for),
which elements it would cover, and what is missing for it to be supported — read from what the
architecture declares, never assumed. A strategy whose prerequisites are not modeled is presented
as such, with what to declare; one that no model here can plan (canary, expand-and-contract,
strangler) is registered as unsupported, so a request for it is answered, never fabricated.

Strategies are never chosen by a score: the plan follows the strategy the request prefers when it
is supported, otherwise the documented default (in-place: the changes carried out on the components
as they are, with what that implies stated). Every alternative is shown side by side with its
trade-offs.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from core.architecture_ir.diff import ChangeKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.components.capabilities import CAPABILITIES
from core.domain.evolution.values import EvidenceSource
from core.domain.migrations.changes import Aspect, ChangeAnalysis, Interpretation, MigrationChange
from core.domain.migrations.entities import MigrationRequest
from core.domain.migrations.plans import PlanFinding, StrategyOption
from core.domain.migrations.steps import Trace
from core.domain.migrations.values import (
    DowntimeStatus,
    FindingType,
    Reversibility,
    RiskCategory,
    StepType,
    TraceKind,
)

DEFAULT_STRATEGY = "in_place"


class PatternKind(StrEnum):
    TEMPLATE = "template"  # how one change is carried out
    STRATEGY = "strategy"  # how the plan as a whole is carried out


class DuplicatePattern(ValueError):
    """A programming error: two patterns registered under one id."""


@dataclass(frozen=True, slots=True)
class PatternMeta:
    id: str
    version: int
    name: str
    description: str
    kind: PatternKind
    changes: tuple[ChangeKind, ...]  # the change kinds it applies to
    aspects: tuple[Aspect, ...]  # it applies to a change touching any of these
    evidence: tuple[EvidenceSource, ...]  # the evidence it requires
    preconditions: tuple[str, ...]
    capabilities: tuple[str, ...]  # component capabilities it relies on (catalog vocabulary)
    steps: tuple[StepType, ...]  # the step types it produces, in order
    dependency_rules: tuple[str, ...]
    risks: tuple[RiskCategory, ...]
    tradeoffs: tuple[str, ...]
    downtime: DowntimeStatus  # when its prerequisites hold
    reversibility: Reversibility
    rollback: str
    data: str | None  # its data-migration requirements, when it moves data
    validation: tuple[str, ...]
    unsupported: tuple[str, ...]
    supported: bool = True  # False: registered so a request for it is answered, never planned

    def __post_init__(self) -> None:
        unknown = [c for c in self.capabilities if c not in CAPABILITIES]
        if unknown:
            raise ValueError(
                f"pattern {self.id} relies on capabilities the catalog does not define: {unknown}"
            )
        if self.supported and not self.steps:
            raise ValueError(f"pattern {self.id} must declare the steps it produces")
        if not self.supported and not self.unsupported:
            raise ValueError(f"pattern {self.id} must say why it is not supported")

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def trace(self) -> Trace:
        return Trace(TraceKind.PATTERN, self.ref, self.name)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "kind": self.kind.value,
            "supported": self.supported,
            "changes": [c.value for c in self.changes],
            "aspects": [a.value for a in self.aspects],
            "evidence": [e.value for e in self.evidence],
            "preconditions": list(self.preconditions),
            "capabilities": list(self.capabilities),
            "steps": [s.value for s in self.steps],
            "dependency_rules": list(self.dependency_rules),
            "risks": [r.value for r in self.risks],
            "tradeoffs": list(self.tradeoffs),
            "downtime": self.downtime.value,
            "reversibility": self.reversibility.value,
            "rollback": self.rollback,
            "data": self.data,
            "validation": list(self.validation),
            "unsupported": list(self.unsupported),
        }


@dataclass(frozen=True)
class PlanningContext:
    """What a pattern may read: both architectures (never modified), the classified changes and the
    request as stated."""

    source: ArchitectureIR
    target: ArchitectureIR
    analysis: ChangeAnalysis
    request: MigrationRequest

    @staticmethod
    def node(ir: ArchitectureIR, node_id: str) -> Node | None:
        return next((n for n in ir.nodes if n.id == node_id), None)

    def plannable(self) -> tuple[MigrationChange, ...]:
        """The migration-relevant changes a pattern can plan (not those for a person, or unsupported)."""
        return tuple(c for c in self.analysis.relevant if c.interpretation is Interpretation.SUPPORTED)

    @staticmethod
    def incoming(ir: ArchitectureIR, node_id: str) -> tuple[Node, ...]:
        """The nodes with a connection to ``node_id``."""
        sources = {c.source_id for c in ir.connections if c.target_id == node_id}
        return tuple(n for n in ir.nodes if n.id in sources)


@dataclass(frozen=True, slots=True)
class Assessment:
    applies: bool
    subjects: tuple[str, ...] = ()  # the elements it would cover
    missing: tuple[str, ...] = ()  # what must be declared for it to be supported
    reasons: tuple[str, ...] = ()  # why it does not apply


class Pattern(Protocol):
    @property
    def meta(self) -> PatternMeta: ...

    def assess(self, context: PlanningContext) -> Assessment: ...


class Registry:
    """The patterns this ArchitectOS ships. Built once, in code; read-only afterwards."""

    def __init__(self, patterns: Iterable[Pattern] = ()) -> None:
        self._patterns: dict[str, Pattern] = {}
        for pattern in patterns:
            self.register(pattern)

    def register(self, pattern: Pattern) -> None:
        if pattern.meta.id in self._patterns:
            raise DuplicatePattern(f"pattern {pattern.meta.id!r} is already registered")
        self._patterns[pattern.meta.id] = pattern

    def get(self, pattern_id: str) -> Pattern | None:
        return self._patterns.get(pattern_id)

    def patterns(self, kind: PatternKind | None = None) -> tuple[Pattern, ...]:
        """Every pattern (of a kind), by id."""
        return tuple(p for _, p in sorted(self._patterns.items()) if kind is None or p.meta.kind is kind)

    def versions(self) -> dict[str, int]:
        return {pattern_id: p.meta.version for pattern_id, p in sorted(self._patterns.items())}


def _option(pattern: Pattern, context: PlanningContext) -> StrategyOption:
    meta = pattern.meta
    if not meta.supported:
        return StrategyOption(meta.ref, meta.name, applies=False, supported=False, tradeoffs=meta.unsupported)
    assessment = pattern.assess(context)
    supported = assessment.applies and not assessment.missing
    return StrategyOption(
        pattern=meta.ref,
        name=meta.name,
        applies=assessment.applies,
        supported=supported,
        subjects=assessment.subjects,
        missing=assessment.missing,
        tradeoffs=(*meta.tradeoffs, *assessment.reasons),
        downtime=meta.downtime if supported else DowntimeStatus.UNKNOWN,
        reversibility=meta.reversibility if supported else Reversibility.UNKNOWN,
    )


def strategy_options(context: PlanningContext, registry: Registry) -> tuple[StrategyOption, ...]:
    """Every strategy, assessed against this context, side by side."""
    return tuple(_option(p, context) for p in registry.patterns(PatternKind.STRATEGY))


def choose(
    options: tuple[StrategyOption, ...], preferred: str | None
) -> tuple[StrategyOption | None, tuple[PlanFinding, ...]]:
    """The strategy the plan follows: the preferred one when supported, otherwise the documented
    default. A preferred strategy that is not supported is a finding naming what is missing."""
    by_id = {o.pattern.split("@")[0]: o for o in options}
    findings: list[PlanFinding] = []
    if preferred is not None:
        option = by_id.get(preferred)
        if option is not None and option.supported:
            return option, ()
        if option is None:
            message = f"The preferred strategy {preferred} is not known."
        else:
            reasons = "; ".join(option.tradeoffs) if not option.applies else ""
            message = f"The preferred strategy {option.name} is not supported for these changes"
            message += f": {reasons}" if reasons else "."
        missing = option.missing if option else ()
        findings.append(PlanFinding(FindingType.STRATEGY_NOT_SUPPORTED, preferred, message, missing=missing))
    default = by_id.get(DEFAULT_STRATEGY)
    return (default if default is not None and default.supported else None), tuple(findings)
