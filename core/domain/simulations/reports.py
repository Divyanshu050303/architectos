"""A simulation as stored and read back: everything but its component outcomes and deltas, which are
read page by page. What is needed to reproduce and explain it is kept: the request's inputs (the
scenario snapshot among them), the overlay it evaluated, the engine and model versions, the
fingerprints, the summary, the runs, the entry impacts, the assumptions, the trace, what could not
be established and the limitations. ``result`` rebuilds the full result from the stored rows and
refuses one that no longer matches its fingerprint."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from core.domain.engine_results import Evidence, Limitation, ModelSet, Unsupported

from .entities import Simulation
from .errors import InvalidSimulationResult
from .results import AnalysisRun, ComponentOutcome, Delta, EntryImpact, SimulationResult


@dataclass(frozen=True, slots=True)
class SimulationReport:
    simulation: Simulation  # without its result
    inputs: Mapping[str, Any]  # the request's inputs, the requirements read, provider and currency
    overlay: Mapping[str, Any] | None = None  # the overlay evaluated; None when nothing was evaluated
    engine_set: ModelSet | None = None
    context_fingerprint: str | None = None
    scenario_fingerprint: str | None = None
    result_fingerprint: str | None = None
    summary: Mapping[str, Any] | None = None
    runs: tuple[AnalysisRun, ...] = ()
    entries: tuple[EntryImpact, ...] = ()
    assumptions: tuple[Evidence, ...] = ()
    trace: tuple[Evidence, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    @classmethod
    def of(
        cls, simulation: Simulation, inputs: Mapping[str, Any], overlay: Mapping[str, Any] | None
    ) -> SimulationReport:
        """The report of a simulation just executed (its result still attached)."""
        result = simulation.result
        bare = replace(simulation, result=None)
        if result is None:
            return cls(bare, inputs)
        return cls(
            bare,
            inputs,
            overlay,
            result.engine_set,
            result.context_fingerprint,
            result.scenario_fingerprint,
            result.fingerprint,
            result.summary(),
            result.runs,
            result.entries,
            result.assumptions,
            result.trace,
            result.unsupported,
            result.limitations,
        )

    def result(self, components: tuple[ComponentOutcome, ...], deltas: tuple[Delta, ...]) -> SimulationResult:
        """The full result, rebuilt from this report and its rows; refused if it no longer matches."""
        if self.engine_set is None or self.context_fingerprint is None or self.scenario_fingerprint is None:
            raise InvalidSimulationResult(details={"fields": ["result"]})
        rebuilt = SimulationResult(
            self.engine_set,
            self.scenario_fingerprint,
            self.context_fingerprint,
            self.runs,
            components,
            self.entries,
            deltas,
            self.assumptions,
            self.trace,
            self.unsupported,
            self.limitations,
        )
        if rebuilt.fingerprint != self.result_fingerprint:
            raise InvalidSimulationResult(details={"fields": ["result_fingerprint"]})
        return rebuilt
