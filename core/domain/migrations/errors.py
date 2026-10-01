from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidMigrationRequest(DomainError):
    """``details`` = {"field", "reason"} (and the element or reference concerned, when there is one)."""

    code = "invalid_migration_request"
    message = "The migration planning request is invalid."


class InvalidMigrationPlan(InvalidEngineResult):
    """A plan with malformed parts (an engine bug, or a corrupted record). ``details`` = {"fields": [...]}."""

    code = "invalid_migration_plan"
    message = "A migration plan is malformed."


class MigrationPlanNotFound(DomainError):
    """No such migration plan (or version) in this project — also when it belongs to another project
    or tenant: indistinguishable on purpose."""

    code = "migration_plan_not_found"
    message = "Migration plan not found."


class InvalidPlanTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_migration_plan_transition"
    message = "The migration plan cannot move to that status from its current status."


class PlanVersionMismatch(DomainError):
    """A review that does not name the exact version it reviewed: ``details`` = {"version", "reason"}
    (``version_mismatch`` or ``content_mismatch``)."""

    code = "migration_plan_version_mismatch"
    message = "The review does not refer to the exact content of this migration plan version."


class StaleMigrationPlan(DomainError):
    """A version whose source, target, models or evidence changed: ``details`` = {"reasons": [...]}."""

    code = "stale_migration_plan"
    message = "The migration plan is stale and must be regenerated before it is reviewed."


class ReviewedPlanNotReplaced(DomainError):
    """Regeneration would replace a version under review or approved without being asked to:
    ``details`` = {"version", "status"}."""

    code = "reviewed_migration_plan"
    message = (
        "The latest version of this migration plan is under review or approved; replacing it must be "
        "explicit."
    )
