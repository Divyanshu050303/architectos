"""What a simulation produces: the baseline and the scenario evaluated by the same engines, in the
same run, and compared — model-based projections, never measurements or predictions.

- A **run** says what one analysis (capacity, reliability, cost) established: ``completed``,
  ``partial``, ``unsupported`` (with the reason) or ``failed``, with the engine's model set and the
  baseline and scenario result fingerprints (traceable to that engine's own result).
- A **delta** compares one metric of one element (or of the ``system``) between baseline and
  scenario, with its unit. Its difference exists only when both values are known; its percentage
  change only when the baseline is known and not zero. A delta that cannot be compared says why
  (``note``); an unknown value is ``None``, never 0.
- An **entry impact** says what the scenario's failures do to one entry point (``Impact``), which
  unavailable elements it requires, and what the architecture would need to declare to decide.
- A **component outcome** says whether a component is unavailable in the scenario, which of its
  properties the scenario changed, and its failure impact.

Nothing is a score. The status follows the runs: no successful run is ``failed`` (if one failed) or
``unsupported``; every requested run completed with nothing unknown or unsupported is
``completed``; anything else is ``partial``.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Self

from core.domain.capacity.units import PRECISION
from core.domain.engine_results import (
    MAX_ID,
    Evidence,
    Limitation,
    ModelSet,
    Unsupported,
    read_evidence,
    text_problem,
)
from core.domain.numbers import arithmetic
from core.domain.requirements.value_objects import decimal_to_str

from .errors import InvalidSimulationResult
from .values import AnalysisKind, Impact, RunState, SimulationStatus

SYSTEM = "system"  # the element id of what concerns the whole architecture
CODE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
UNIT = re.compile(r"^[A-Za-z0-9%/._ -]{1,32}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
MAX_ITEMS = 5000  # components, entries, unsupported and evidence of one simulation (bounded by the IR)
MAX_DELTAS = 20_000  # the hard cap of limits.SimulationLimits.max_deltas


def _check(problems: list[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidSimulationResult(details={"fields": found})


def _id_problem(value: object, name: str) -> str | None:
    return None if isinstance(value, str) and 0 < len(value) <= MAX_ID else name


def _ids_problem(values: object, name: str) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ITEMS:
        return name
    return None if all(isinstance(v, str) and 0 < len(v) <= MAX_ID for v in values) else name


def _code_problem(value: object, name: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    return None if isinstance(value, str) and CODE.fullmatch(value) else name


def _number_problem(value: object, name: str) -> str | None:
    if value is None:
        return None
    return None if isinstance(value, Decimal) and value.is_finite() else name


def _fingerprint_problem(value: object, name: str) -> str | None:
    return None if isinstance(value, str) and FINGERPRINT.fullmatch(value) else name


def _number(value: Decimal | None) -> str | None:
    return decimal_to_str(value) if value is not None else None


def _read_number(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        raise InvalidSimulationResult(details={"fields": ["number"]}) from None


def _evidence_problem(values: object, name: str) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ITEMS:
        return name
    ok = all(
        isinstance(e, Evidence) and isinstance(e.label, str) and isinstance(e.value, str) for e in values
    )
    return None if ok else name


def _rounded(value: Decimal) -> Decimal:
    """Half-even to the 9 decimal places quantities keep."""
    return value.quantize(PRECISION).normalize() + 0


@dataclass(frozen=True, slots=True)
class Delta:
    """One metric of one element, baseline versus scenario, in one unit."""

    analysis: AnalysisKind
    element_id: str  # a node, a connection, or SYSTEM
    metric: str  # e.g. "work_rate.demand", "availability", "monthly_cost"
    unit: str
    baseline: Decimal | None
    scenario: Decimal | None
    note: str | None = None  # why it is not comparable, when it is not

    def __post_init__(self) -> None:
        _check(
            [
                None if isinstance(self.analysis, AnalysisKind) else "analysis",
                _id_problem(self.element_id, "element_id"),
                _code_problem(self.metric, "metric"),
                None if isinstance(self.unit, str) and UNIT.fullmatch(self.unit) else "unit",
                _number_problem(self.baseline, "baseline"),
                _number_problem(self.scenario, "scenario"),
                _code_problem(self.note, "note", required=False),
            ]
        )

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.analysis.value, self.element_id, self.metric)

    @property
    def comparable(self) -> bool:
        return self.note is None and self.baseline is not None and self.scenario is not None

    @property
    def difference(self) -> Decimal | None:
        """scenario - baseline, when both are known and comparable."""
        if self.note is not None or self.baseline is None or self.scenario is None:
            return None
        with arithmetic():
            return _rounded(self.scenario - self.baseline)

    @property
    def percentage(self) -> Decimal | None:
        """The change in percent of the baseline: only when comparable and the baseline is not 0."""
        difference = self.difference
        if difference is None or self.baseline is None or self.baseline == 0:
            return None
        with arithmetic():
            return _rounded(difference / self.baseline * 100)

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis": self.analysis.value,
            "element_id": self.element_id,
            "metric": self.metric,
            "unit": self.unit,
            "baseline": _number(self.baseline),
            "scenario": _number(self.scenario),
            "difference": _number(self.difference),
            "percentage": _number(self.percentage),
            "comparable": self.comparable,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                AnalysisKind(data["analysis"]),
                data["element_id"],
                data["metric"],
                data["unit"],
                _read_number(data.get("baseline")),
                _read_number(data.get("scenario")),
                data.get("note"),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidSimulationResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class EntryImpact:
    """What the scenario's failures do to one entry point, from declared semantics."""

    entry_id: str
    impact: Impact
    explanation: str
    through: tuple[str, ...] = ()  # the unavailable elements it requires (directly or not)
    missing: tuple[str, ...] = ()  # what would decide an unknown impact

    def __post_init__(self) -> None:
        for name in ("through", "missing"):
            value = getattr(self, name)
            if isinstance(value, tuple):
                object.__setattr__(self, name, tuple(sorted(set(value))))
        _check(
            [
                _id_problem(self.entry_id, "entry_id"),
                None if isinstance(self.impact, Impact) else "impact",
                text_problem(self.explanation, "explanation"),
                _ids_problem(self.through, "through"),
                _ids_problem(self.missing, "missing"),
                None if self.impact is not Impact.UNKNOWN or self.missing else "missing",
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "impact": self.impact.value,
            "explanation": self.explanation,
            "through": list(self.through),
            "missing": list(self.missing),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                data["entry_id"],
                Impact(data["impact"]),
                data["explanation"],
                tuple(data.get("through") or ()),
                tuple(data.get("missing") or ()),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidSimulationResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class ComponentOutcome:
    """One component in the scenario: unavailable or not, what the scenario changed on it (as
    ``property: baseline -> scenario`` evidence), and its failure impact (None: no failure)."""

    node_id: str
    unavailable: bool = False
    changes: tuple[Evidence, ...] = ()
    impact: Impact | None = None

    def __post_init__(self) -> None:
        if isinstance(self.changes, tuple):
            object.__setattr__(self, "changes", tuple(sorted(self.changes, key=lambda e: e.label)))
        _check(
            [
                _id_problem(self.node_id, "node_id"),
                None if isinstance(self.unavailable, bool) else "unavailable",
                _evidence_problem(self.changes, "changes"),
                None if self.impact is None or isinstance(self.impact, Impact) else "impact",
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "unavailable": self.unavailable,
            "changes": [e.to_dict() for e in self.changes],
            "impact": self.impact.value if self.impact is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            impact = data.get("impact")
            return cls(
                data["node_id"],
                bool(data.get("unavailable")),
                read_evidence(data.get("changes")),
                Impact(impact) if impact is not None else None,
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidSimulationResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    """What one engine established on the baseline and on the scenario of this simulation."""

    analysis: AnalysisKind
    state: RunState
    model_set: ModelSet | None = None  # the engine's models: the same for both sides by construction
    baseline_fingerprint: str | None = None  # the engine's own result fingerprints
    scenario_fingerprint: str | None = None
    reason: str | None = None  # a code, for unsupported and failed runs
    message: str | None = None

    def __post_init__(self) -> None:
        ran = self.state in {RunState.COMPLETED, RunState.PARTIAL}
        _check(
            [
                None if isinstance(self.analysis, AnalysisKind) else "analysis",
                None if isinstance(self.state, RunState) else "state",
                None if not ran or isinstance(self.model_set, ModelSet) else "model_set",
                None if not ran else _fingerprint_problem(self.baseline_fingerprint, "baseline_fingerprint"),
                None if not ran else _fingerprint_problem(self.scenario_fingerprint, "scenario_fingerprint"),
                None if ran or self.reason is not None else "reason",
                _code_problem(self.reason, "reason", required=False),
                text_problem(self.message, "message", required=False),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis": self.analysis.value,
            "state": self.state.value,
            "model_set": self.model_set.to_dict() if self.model_set is not None else None,
            "baseline_fingerprint": self.baseline_fingerprint,
            "scenario_fingerprint": self.scenario_fingerprint,
            "reason": self.reason,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            model_set = data.get("model_set")
            return cls(
                AnalysisKind(data["analysis"]),
                RunState(data["state"]),
                ModelSet.from_dict(model_set) if model_set is not None else None,
                data.get("baseline_fingerprint"),
                data.get("scenario_fingerprint"),
                data.get("reason"),
                data.get("message"),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidSimulationResult(details={"fields": [type(error).__name__]}) from None


def _unique(items: tuple[Any, ...], key: Callable[[Any], Any], name: str) -> str | None:
    keys = [key(i) for i in items]
    return None if len(keys) == len(set(keys)) else name


_ORDERS: dict[str, Callable[[Any], Any]] = {
    "runs": lambda r: r.analysis.value,
    "components": lambda c: c.node_id,
    "entries": lambda e: e.entry_id,
    "deltas": lambda d: d.key,
    "assumptions": lambda e: e.label,
    "unsupported": lambda u: (u.element_id, u.code),
    "limitations": lambda x: x.code,
}


@dataclass(frozen=True, slots=True)
class SimulationResult:
    engine_set: ModelSet  # the simulation engine's own steps and versions
    scenario_fingerprint: str
    context_fingerprint: str  # every input besides the architecture's content
    runs: tuple[AnalysisRun, ...] = ()
    components: tuple[ComponentOutcome, ...] = ()
    entries: tuple[EntryImpact, ...] = ()
    deltas: tuple[Delta, ...] = ()
    assumptions: tuple[Evidence, ...] = ()  # stated, never computed with
    trace: tuple[Evidence, ...] = ()  # how the scenario was applied and evaluated, in order
    unsupported: tuple[Unsupported, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    def __post_init__(self) -> None:
        for name, key in _ORDERS.items():
            items = getattr(self, name)
            if isinstance(items, tuple) and all(hasattr(i, "__hash__") for i in items):
                unique = tuple(set(items)) if name in {"unsupported", "limitations"} else items
                object.__setattr__(self, name, tuple(sorted(unique, key=key)))
        sized = ("runs", "components", "entries", "unsupported", "limitations")
        _check(
            [
                None if len(self.deltas) <= MAX_DELTAS else "deltas",
                None if isinstance(self.engine_set, ModelSet) else "engine_set",
                _fingerprint_problem(self.scenario_fingerprint, "scenario_fingerprint"),
                _fingerprint_problem(self.context_fingerprint, "context_fingerprint"),
                *(None if len(getattr(self, n)) <= MAX_ITEMS else n for n in sized),
                _unique(self.runs, lambda r: r.analysis, "runs"),
                _unique(self.components, lambda c: c.node_id, "components"),
                _unique(self.entries, lambda e: e.entry_id, "entries"),
                _unique(self.deltas, lambda d: d.key, "deltas"),
                _evidence_problem(self.assumptions, "assumptions"),
                _evidence_problem(self.trace, "trace"),
            ]
        )

    @property
    def status(self) -> SimulationStatus:
        states = [r.state for r in self.runs]
        if not any(s in {RunState.COMPLETED, RunState.PARTIAL} for s in states):
            return SimulationStatus.FAILED if RunState.FAILED in states else SimulationStatus.UNSUPPORTED
        unknown = any(e.impact is Impact.UNKNOWN for e in self.entries)
        if all(s is RunState.COMPLETED for s in states) and not self.unsupported and not unknown:
            return SimulationStatus.COMPLETED
        return SimulationStatus.PARTIAL

    def summary(self) -> dict[str, Any]:
        """Counts only, reproducible from the runs, entries, components and deltas: no score."""
        states = Counter(r.state.value for r in self.runs)
        impacts = Counter(e.impact.value for e in self.entries)
        changed = [d for d in self.deltas if d.difference is not None and d.difference != 0]
        return {
            "runs": {s.value: states.get(s.value, 0) for s in RunState},
            "entries": {i.value: impacts.get(i.value, 0) for i in Impact},
            "components": len(self.components),
            "unavailable": sum(1 for c in self.components if c.unavailable),
            "deltas": {
                "comparable": sum(1 for d in self.deltas if d.comparable),
                "not_comparable": sum(1 for d in self.deltas if not d.comparable),
                "changed": len(changed),
            },
            "unsupported": len(self.unsupported),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_set": self.engine_set.to_dict(),
            "scenario_fingerprint": self.scenario_fingerprint,
            "context_fingerprint": self.context_fingerprint,
            "status": self.status.value,
            "summary": self.summary(),
            "runs": [r.to_dict() for r in self.runs],
            "components": [c.to_dict() for c in self.components],
            "entries": [e.to_dict() for e in self.entries],
            "deltas": [d.to_dict() for d in self.deltas],
            "assumptions": [e.to_dict() for e in self.assumptions],
            "trace": [e.to_dict() for e in self.trace],
            "unsupported": [u.to_dict() for u in self.unsupported],
            "limitations": [x.to_dict() for x in self.limitations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SimulationResult:
        try:
            return cls(
                ModelSet.from_dict(data["engine_set"]),
                data["scenario_fingerprint"],
                data["context_fingerprint"],
                tuple(AnalysisRun.from_dict(r) for r in data.get("runs") or ()),
                tuple(ComponentOutcome.from_dict(c) for c in data.get("components") or ()),
                tuple(EntryImpact.from_dict(e) for e in data.get("entries") or ()),
                tuple(Delta.from_dict(d) for d in data.get("deltas") or ()),
                read_evidence(data.get("assumptions")),
                read_evidence(data.get("trace")),
                tuple(Unsupported.from_dict(u) for u in data.get("unsupported") or ()),
                tuple(Limitation.from_dict(x) for x in data.get("limitations") or ()),
            )
        except (KeyError, TypeError) as error:
            raise InvalidSimulationResult(details={"fields": [type(error).__name__]}) from None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
