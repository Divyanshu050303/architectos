"""The simulation engine: the evaluator contract, the registry and the orchestrator.

A simulation runs in explicit steps, each testable on its own:

1. the scenario is checked against the exact revision (``validation.check_scenario``): an invalid
   scenario is refused before anything is calculated;
2. it is applied to an in-memory copy (``core/domain/simulations/overlay.py``): the revision is never
   modified;
3. each requested analysis is evaluated by its **evaluator**, which calls the engine it wraps (the
   Capacity, Reliability or Cost Engine, through their ports) on the baseline and on the scenario —
   the orchestrator never calculates capacity, reliability or cost itself;
4. the outcomes are collected into one result: runs, component outcomes, entry impacts, deltas,
   what could not be established, the assumptions, and a trace of every step.

An analysis is run only when requested (default: every analysis the scenario concerns) and when its
inputs are there; otherwise its run is ``unsupported`` with the reason (``not_concerned``, the
missing input…), never calculated on invented inputs. An evaluation an engine refuses is reported
``unsupported`` with the engine's error code; one that fails is ``failed`` and logged by the error's
type only; the other analyses still count. Everything is ordered and fingerprinted: equal inputs,
equal result.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from core.domain.engine_results import Evidence, Limitation, ModelSet, Unsupported
from core.domain.errors import DomainError
from core.domain.simulations.errors import InvalidSimulationRequest, InvalidSimulationResult
from core.domain.simulations.overlay import Overlay, apply_scenario
from core.domain.simulations.results import (
    AnalysisRun,
    ComponentOutcome,
    Delta,
    EntryImpact,
    SimulationResult,
)
from core.domain.simulations.values import AnalysisKind, Impact, RunState

from .context import SimulationContext
from .validation import ScenarioPlan, check_scenario

log = logging.getLogger("architectos.simulation")

ENGINE = ("simulation", 1)  # the orchestrator's own version
INPUTS = frozenset({"workload", "pricing"})  # what an evaluator may declare it needs
MODEL_BASED = Limitation(
    "model_based",
    "Simulation results are model-based projections of the architecture as declared. They do not "
    "guarantee real-world performance, availability, cost or failure behavior, and no measurement is read.",
)
NO_DEFAULTS = Limitation(
    "no_defaults",
    "Nothing is assumed that the architecture, the request or the engines' models do not state: missing "
    "inputs stay unknown or unsupported, never zero.",
)
MISSING = {
    "workload": ("no_workload", "No workload profile was given: this analysis is not calculated."),
    "pricing": ("no_pricing", "No pricing inputs were given: the cost is not calculated."),
}


@dataclass(frozen=True, slots=True)
class EvaluatorMeta:
    analysis: AnalysisKind
    version: int
    name: str
    description: str
    requires: tuple[str, ...] = ()  # among INPUTS: without one, the analysis is unsupported
    unsupported: tuple[str, ...] = ()  # what it cannot evaluate, in words

    def __post_init__(self) -> None:
        if not isinstance(self.analysis, AnalysisKind) or self.version < 1 or set(self.requires) - INPUTS:
            raise DuplicateEvaluator(f"inconsistent evaluator declaration: {self.analysis}")

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis": self.analysis.value,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "requires": list(self.requires),
            "unsupported": list(self.unsupported),
        }


@dataclass(frozen=True, slots=True)
class Evaluation:
    """What one evaluator established: its run and, for a run that ran, what it compared."""

    run: AnalysisRun
    deltas: tuple[Delta, ...] = ()
    entries: tuple[EntryImpact, ...] = ()
    impacts: Mapping[str, Impact] = field(default_factory=dict)  # component → impact of the failures
    unsupported: tuple[Unsupported, ...] = ()
    trace: tuple[Evidence, ...] = ()
    assumptions: tuple[Evidence, ...] = ()


class Evaluator(Protocol):
    meta: EvaluatorMeta

    def evaluate(
        self,
        context: SimulationContext,
        overlay: Overlay,
        plan: ScenarioPlan,
        earlier: Mapping[AnalysisKind, Evaluation],
    ) -> Evaluation:
        """Deterministic for equal inputs. ``earlier`` holds the evaluations before this one (cost
        reads capacity's)."""
        ...


class DuplicateEvaluator(ValueError):
    """A programming error: two evaluators for one analysis, or an inconsistent declaration."""


class Registry:
    """The evaluators this engine knows, one per analysis, in their registered order."""

    def __init__(self, evaluators: Iterable[Evaluator]) -> None:
        self._evaluators: dict[AnalysisKind, Evaluator] = {}
        for evaluator in evaluators:
            if evaluator.meta.analysis in self._evaluators:
                raise DuplicateEvaluator(evaluator.meta.analysis.value)
            self._evaluators[evaluator.meta.analysis] = evaluator

    def evaluators(self) -> tuple[Evaluator, ...]:
        return tuple(self._evaluators.values())

    def get(self, analysis: AnalysisKind) -> Evaluator | None:
        return self._evaluators.get(analysis)


def _available(context: SimulationContext, name: str) -> bool:
    if name == "workload":
        return context.request.workload is not None
    return context.request.pricing is not None and context.snapshot is not None


def _unsupported(analysis: AnalysisKind, reason: str, message: str) -> Evaluation:
    return Evaluation(AnalysisRun(analysis, RunState.UNSUPPORTED, reason=reason, message=message))


def _evaluate(
    evaluator: Evaluator,
    context: SimulationContext,
    overlay: Overlay,
    plan: ScenarioPlan,
    earlier: Mapping[AnalysisKind, Evaluation],
) -> Evaluation:
    meta = evaluator.meta
    for name in meta.requires:
        if not _available(context, name):
            return _unsupported(meta.analysis, *MISSING[name])
    try:
        evaluation = evaluator.evaluate(context, overlay, plan, earlier)
        if not isinstance(evaluation, Evaluation) or evaluation.run.analysis is not meta.analysis:
            raise InvalidSimulationResult(details={"fields": ["run"]})
    except InvalidSimulationRequest:
        raise  # the request itself is wrong: refused, nothing stored
    except InvalidSimulationResult:
        log.error("simulation evaluator produced a malformed result", extra={"analysis": meta.analysis.value})
        return Evaluation(AnalysisRun(meta.analysis, RunState.FAILED, reason="invalid_output"))
    except DomainError as error:  # the engine refuses what the scenario asks of it
        message = f"The {meta.analysis.value} engine refused the scenario: {error.message}"
        return _unsupported(meta.analysis, error.code, message)
    except Exception as error:  # one failing evaluator must not take the others down, nor go unnoticed
        log.error(  # no traceback on purpose: its message could carry a configuration value
            "simulation evaluator failed",
            extra={"analysis": meta.analysis.value, "error_type": type(error).__name__},
        )
        message = f"The {meta.analysis.value} evaluation could not complete."
        return Evaluation(AnalysisRun(meta.analysis, RunState.FAILED, reason="engine_error", message=message))
    return evaluation


def _components(overlay: Overlay, evaluations: Iterable[Evaluation]) -> tuple[ComponentOutcome, ...]:
    impacts: dict[str, Impact] = {}
    for evaluation in evaluations:
        impacts.update(evaluation.impacts)
    changes: dict[str, list[Evidence]] = {}
    for change in overlay.changes:
        if overlay.baseline.node(change.element_id) is not None:
            changes.setdefault(change.element_id, []).append(change.evidence())
    concerned = set(changes) | set(overlay.unavailable_nodes) | set(impacts) | set(overlay.undetermined_nodes)
    concerned |= {node for node, _ in overlay.zone_losses}
    return tuple(
        ComponentOutcome(
            node_id,
            unavailable=node_id in overlay.unavailable_nodes,
            changes=tuple(changes.get(node_id, ())),
            impact=impacts.get(node_id),
        )
        for node_id in sorted(concerned)
    )


def _requested(context: SimulationContext, plan: ScenarioPlan) -> tuple[AnalysisKind, ...]:
    asked = context.request.analyses if context.request.analyses is not None else plan.analyses
    return tuple(a for a in AnalysisKind if a in asked)


def _undetermined(overlay: Overlay) -> tuple[Unsupported, ...]:
    return tuple(
        Unsupported(
            node,
            "failure_undetermined",
            f"{node} declares no zone or region: whether the scenario's failure reaches it is unknown.",
            (f"{node}.configuration.availability_zones", f"{node}.configuration.region"),
        )
        for node in overlay.undetermined_nodes
    )


def analyze(context: SimulationContext, registry: Registry) -> SimulationResult:
    """The simulation's result. InvalidSimulationRequest for a scenario that does not fit the
    revision (nothing has run then)."""
    plan = check_scenario(context.ir, context.request)
    overlay = apply_scenario(context.ir, context.revision, context.request.scenario)
    evaluations: dict[AnalysisKind, Evaluation] = {}
    versions: list[tuple[str, int]] = [ENGINE, *plan.types.models]
    for analysis in _requested(context, plan):
        evaluator = registry.get(analysis)
        if evaluator is None:
            reason = ("no_evaluator", f"No {analysis.value} evaluator is available.")
            evaluations[analysis] = _unsupported(analysis, *reason)
            continue
        versions.append((f"evaluator.{analysis.value}", evaluator.meta.version))
        if analysis not in plan.analyses:
            reason = ("not_concerned", f"The scenario changes nothing the {analysis.value} engine reads.")
            evaluations[analysis] = _unsupported(analysis, *reason)
        else:
            evaluations[analysis] = _evaluate(evaluator, context, overlay, plan, dict(evaluations))
    ran = [e for e in evaluations.values() if e.run.model_set is not None]
    stated = tuple(Evidence(f"assumption.{a.key}", a.statement) for a in context.request.assumptions)
    planned = tuple(Evidence(f"plan.{p.key}", f"{p.type.id} v{p.type.version}") for p in plan.parts)
    return SimulationResult(
        engine_set=ModelSet.of(versions),
        scenario_fingerprint=context.request.scenario.fingerprint,
        context_fingerprint=context.fingerprint,
        runs=tuple(e.run for e in evaluations.values()),
        components=_components(overlay, evaluations.values()),
        entries=tuple(i for e in evaluations.values() for i in e.entries),
        deltas=tuple(d for e in evaluations.values() for d in e.deltas),
        assumptions=stated + tuple(x for e in ran for x in e.assumptions),
        trace=overlay.trace() + planned + tuple(x for e in evaluations.values() for x in e.trace),
        unsupported=_undetermined(overlay) + tuple(u for e in evaluations.values() for u in e.unsupported),
        limitations=(MODEL_BASED, NO_DEFAULTS),
    )
