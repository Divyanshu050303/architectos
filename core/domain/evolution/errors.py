from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidEvolutionRequest(DomainError):
    """``details`` = {"field", "reason"} (and the element or goal referred to, when there is one)."""

    code = "invalid_evolution_request"
    message = "The evolution analysis request is invalid."


class InvalidEvolutionResult(InvalidEngineResult):
    """A result with malformed fields (an engine bug, or a corrupted record). ``details`` =
    {"fields": [...]}."""

    code = "invalid_evolution_result"
    message = "An evolution analysis result is malformed."


class EvolutionAnalysisNotFound(DomainError):
    """No such evolution analysis for this architecture (also when it belongs to another
    architecture, project or tenant: indistinguishable on purpose)."""

    code = "evolution_analysis_not_found"
    message = "Evolution analysis not found."


class CandidateNotFound(DomainError):
    """No such candidate in this evolution analysis."""

    code = "evolution_candidate_not_found"
    message = "Evolution candidate not found."


class InvalidEvolutionTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_evolution_transition"
    message = "The evolution analysis cannot move to that status from its current status."


class InvalidCandidate(DomainError):
    """A candidate that cannot be applied to its baseline as an overlay. ``details`` = {"candidate_id",
    "reason"} and, when there is one, the ``element_id`` and ``property`` concerned — enough to act on
    (e.g. ``unknown_element``, ``not_applicable``, ``baseline_mismatch``, an IR rule broken)."""

    code = "invalid_evolution_candidate"
    message = "The candidate cannot be applied to its baseline."
