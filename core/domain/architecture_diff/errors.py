from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidDiffRequest(DomainError):
    """``details`` = {"field", "reason"}: a comparison that cannot be made as asked."""

    code = "invalid_diff_request"
    message = "The architecture comparison request is invalid."


class InvalidDiffRecord(InvalidEngineResult):
    """A change, group, impact, explanation or diff with malformed parts (a bug, or a corrupted record).
    ``details`` = {"fields": [...]}."""

    code = "invalid_diff_record"
    message = "An architecture diff record is malformed."


class ArchitectureDiffNotFound(DomainError):
    """No such diff in this project — also when it belongs to another project or tenant."""

    code = "architecture_diff_not_found"
    message = "Architecture diff not found."


class ComparedStateNotFound(DomainError):
    """``details`` = {"side"}: a compared state does not exist in this project, is not visible to the
    caller, or is not comparable (an agent run without a candidate). One answer for all three, so a
    hidden state cannot be told apart from a missing one."""

    code = "compared_state_not_found"
    message = "A compared architecture state was not found."


class DiffTooLarge(DomainError):
    """``details`` = {"limit", "value"}: the comparison exceeds a bound (changes, elements)."""

    code = "architecture_diff_too_large"
    message = "The comparison is too large to analyze."
