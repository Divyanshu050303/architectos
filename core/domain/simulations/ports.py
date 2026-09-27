"""What the simulation service needs from the engine, so the domain never imports it."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.cost.pricing import PricingSnapshot
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import RevisionInfo

from .entities import SimulationRequest
from .results import SimulationResult


@dataclass(frozen=True, slots=True)
class SimulationOutput:
    result: SimulationResult
    overlay: Mapping[str, Any]  # the overlay evaluated (overlay.Overlay.to_dict): stored to reconstruct it


class SimulationEngine(Protocol):
    def simulate(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: SimulationRequest,
        requirements: tuple[Requirement, ...],
        snapshot: PricingSnapshot | None,
        provider: str | None,
        currency: str | None,
    ) -> SimulationOutput:
        """Deterministic for equal inputs. Raises InvalidSimulationRequest for a scenario the revision
        or the limits do not allow (nothing has run then)."""
        ...

    def catalog(self) -> Mapping[str, Any]:
        """The scenario types, the evaluators and the execution limits."""
        ...
