from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidMoney(DomainError):
    """An amount or currency the cost engine cannot use. ``details`` = {"field", "reason"}."""

    code = "invalid_money"
    message = "A monetary amount or currency is invalid."


class CurrencyMismatch(DomainError):
    """Amounts in different currencies were combined; they never are (no conversion)."""

    code = "currency_mismatch"
    message = "Amounts in different currencies cannot be combined."


class InvalidPricingRecord(DomainError):
    """``details`` = {"field", "reason"} (and ``record_id`` inside a snapshot)."""

    code = "invalid_pricing_record"
    message = "A pricing record is invalid."


class PricingSnapshotNotFound(DomainError):
    """No such snapshot in this organization (also when it belongs to another: indistinguishable)."""

    code = "pricing_snapshot_not_found"
    message = "Pricing snapshot not found."


class InvalidPricingSnapshot(DomainError):
    """``details`` = {"field", "reason"}."""

    code = "invalid_pricing_snapshot"
    message = "The pricing snapshot is invalid."


class InvalidCostRequest(DomainError):
    """``details`` = {"field", "reason"}."""

    code = "invalid_cost_request"
    message = "The cost analysis request is invalid."


class InvalidCostResult(InvalidEngineResult):
    """A line item or result with malformed fields (a model bug, or a corrupted record).
    ``details`` = {"fields": [...]}."""

    code = "invalid_cost_result"
    message = "A cost result is malformed."


class InvalidCostAnalysisTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_cost_analysis_transition"
    message = "The cost analysis cannot move to that status from its current status."


class IncompatibleCapacityAnalysis(DomainError):
    """The cited capacity analysis cannot supply this cost analysis's usage: another architecture,
    another revision (number or content), or no result (failed or unfinished).
    ``details`` = {"reason": ...}."""

    code = "incompatible_capacity_analysis"
    message = "The capacity analysis does not describe the architecture revision being costed."
