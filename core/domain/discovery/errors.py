from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidDiscoveryRequest(DomainError):
    """``details`` = {"field", "reason"} (and the artifact concerned, when there is one)."""

    code = "invalid_discovery_request"
    message = "The discovery request is invalid."


class InvalidDiscoveryResult(InvalidEngineResult):
    """A discovery result with malformed parts (an engine bug, or a corrupted record).
    ``details`` = {"fields": [...]}."""

    code = "invalid_discovery_result"
    message = "A discovery result is malformed."


class DiscoveryRunNotFound(DomainError):
    """No such discovery run in this project — also when it belongs to another project or tenant:
    indistinguishable on purpose."""

    code = "discovery_run_not_found"
    message = "Discovery run not found."


class InvalidDiscoveryTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_discovery_transition"
    message = "The discovery run cannot move to that status from its current status."


class ProposalNotAcceptable(DomainError):
    """``details`` = {"reason": "nothing_to_accept" | "structurally_invalid" | "proposal_changed"}."""

    code = "discovery_proposal_not_acceptable"
    message = "The discovery proposal cannot be accepted as it is."


class AcceptedRunNotDeleted(DomainError):
    """A run whose proposal was accepted is the provenance of that revision: it is kept."""

    code = "accepted_discovery_run"
    message = "A discovery run whose proposal was accepted cannot be deleted."


class DiscoveryRunInUse(DomainError):
    """A run a drift analysis compared is that analysis's evidence: it is kept."""

    code = "discovery_run_in_use"
    message = "A discovery run a drift analysis compared cannot be deleted."
