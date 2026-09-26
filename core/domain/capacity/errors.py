from core.domain.errors import DomainError


class InvalidQuantity(DomainError):
    """A number or unit the capacity engine cannot use. ``details`` = {"field", "reason"}."""

    code = "invalid_capacity_quantity"
    message = "A capacity quantity is invalid."


class InvalidWorkload(DomainError):
    """A workload profile with missing, contradictory or out-of-range values.
    ``details`` = {"field", "reason"}."""

    code = "invalid_workload_profile"
    message = "The workload profile is invalid."


class InvalidCapacityResult(DomainError):
    """A capacity result or estimate with malformed fields (a model bug, or a corrupted record).
    ``details`` = {"fields": [...]}."""

    code = "invalid_capacity_result"
    message = "A capacity result is malformed."


class InvalidAnalysisTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_capacity_analysis_transition"
    message = "The capacity analysis cannot move to that status from its current status."


class InvalidCapacityConfig(DomainError):
    """An analysis asks for what the engine does not offer: an unknown model, parameters of a
    model not selected, an undeclared parameter or an invalid value.
    ``details`` = {"reason", "model_id", "parameter"}."""

    code = "invalid_capacity_config"
    message = "The capacity analysis configuration is invalid."


class InvalidScenario(DomainError):
    """A scenario that cannot be applied: invalid growth, a target in the wrong unit, a change to
    something that does not exist or to a value the IR refuses. ``details`` = {"field", "reason"}
    (and ``index`` for the scenario)."""

    code = "invalid_capacity_scenario"
    message = "The capacity scenario is invalid."


class CapacityAnalysisNotFound(DomainError):
    """No such analysis for this architecture (also when it belongs to another architecture,
    project or tenant: indistinguishable on purpose)."""

    code = "capacity_analysis_not_found"
    message = "Capacity analysis not found."
