"""Execution limits of a simulation: configurable, within hard caps, checked before expensive work.

Simulations are synchronous and deterministic, so they are bounded by size, never by a clock (a
time-based cut-off would make equal inputs give different results):

- ``max_changes`` / ``max_failures``: parts a scenario may have (hard caps 50 and 50);
- ``max_affected_components``: components a scenario's failures may make unavailable, lose a zone of,
  or leave undetermined (hard cap: the IR's node limit) — refused before any engine runs;
- ``max_deltas``: comparisons a result lists; beyond it, the deltas are cut in their canonical order and
  the cut is reported (``deltas_truncated``), never silent.

The work itself is bounded by the architecture's size (1,000 nodes, 5,000 connections): every graph
search visits each element once, the reliability evaluator judges each component once, and no model
iterates (compound growth is a closed form over at most 120 periods). Requests are rate-limited by
the API. A request over a limit fails with ``invalid_simulation_request`` naming the limit.
"""

from dataclasses import dataclass

from core.architecture_ir.model import MAX_NODES

from .errors import InvalidSimulationRequest
from .results import MAX_DELTAS
from .scenarios import MAX_CHANGES, MAX_FAILURES, Scenario


def _refused(field: str, reason: str, limit: int) -> InvalidSimulationRequest:
    return InvalidSimulationRequest(details={"field": field, "reason": reason, "limit": limit})


@dataclass(frozen=True, slots=True)
class SimulationLimits:
    max_changes: int = MAX_CHANGES
    max_failures: int = MAX_FAILURES
    max_affected_components: int = MAX_NODES
    max_deltas: int = MAX_DELTAS

    def __post_init__(self) -> None:
        for name, cap in (
            ("max_changes", MAX_CHANGES),
            ("max_failures", MAX_FAILURES),
            ("max_affected_components", MAX_NODES),
            ("max_deltas", MAX_DELTAS),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
                raise ValueError(f"{name} must be between 1 and {cap}")

    def check(self, scenario: Scenario) -> None:
        """Before anything is applied or calculated."""
        if len(scenario.changes) > self.max_changes:
            raise _refused("scenario.changes", "too_many", self.max_changes)
        if len(scenario.failures) > self.max_failures:
            raise _refused("scenario.failures", "too_many", self.max_failures)

    def check_affected(self, affected: int) -> None:
        """After the failures are resolved, before any engine runs."""
        if affected > self.max_affected_components:
            raise _refused("scenario.failures", "too_many_affected", self.max_affected_components)

    def to_dict(self) -> dict[str, int]:
        return {
            "max_changes": self.max_changes,
            "max_failures": self.max_failures,
            "max_affected_components": self.max_affected_components,
            "max_deltas": self.max_deltas,
        }


DEFAULT_LIMITS = SimulationLimits()
