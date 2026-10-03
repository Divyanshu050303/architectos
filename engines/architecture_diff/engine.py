"""The deterministic comparison of two architecture states: the semantic diff, the engines on both
states, then what the changes touch (requirements through traces and the validation verdicts, ADRs
through their related elements). No model is involved; the same states and inputs always give the same
outcome."""

from core.domain.architecture_diff.ports import DiffInputs, DiffOutcome, ResolvedState

from .impact import DiffEngines, ImpactComparer, State
from .semantic import semantic_diff
from .traceability import traceability


def _state(resolved: ResolvedState) -> State:
    return State(resolved.ir, resolved.analyzed_as, resolved.number, resolved.compared.content_hash)


class DeterministicDiffEngine:
    """Implements ``DiffComputer``."""

    def __init__(self, engines: DiffEngines) -> None:
        self._impacts = ImpactComparer(engines)

    def compare(self, base: ResolvedState, target: ResolvedState, inputs: DiffInputs) -> DiffOutcome:
        semantic = semantic_diff(base.ir, target.ir)  # DiffTooLarge before any engine runs
        impacts = self._impacts.compare(_state(base), _state(target), inputs.impact)
        traced = traceability(
            base.ir,
            target.ir,
            semantic,
            inputs.requirements,
            inputs.decisions,
            impacts.verdicts,
            inputs.scope,
        )
        return DiffOutcome(semantic, traced.requirements, traced.decisions, impacts.engines, traced.unknowns)
