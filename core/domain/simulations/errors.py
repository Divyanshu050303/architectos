from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidSimulationRequest(DomainError):
    """``details`` = {"field", "reason"} (and the element referred to, when there is one)."""

    code = "invalid_simulation_request"
    message = "The simulation request is invalid."


class InvalidSimulationResult(InvalidEngineResult):
    """A result with malformed fields (an engine bug, or a corrupted record). ``details`` =
    {"fields": [...]}."""

    code = "invalid_simulation_result"
    message = "A simulation result is malformed."


class SimulationNotFound(DomainError):
    """No such simulation for this architecture (also when it belongs to another architecture,
    project or tenant: indistinguishable on purpose)."""

    code = "simulation_not_found"
    message = "Simulation not found."


class InvalidSimulationTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_simulation_transition"
    message = "The simulation cannot move to that status from its current status."
