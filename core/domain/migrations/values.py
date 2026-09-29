"""The vocabulary of migration planning. A migration plan is a **proposal** for engineering review:
nothing here records that a step was executed, and no status implies it. There is no execution
status in this domain — executing a migration is a separate, explicitly authorized workflow.
"""

import hashlib
import json
import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from .errors import InvalidMigrationPlan

CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
MAX_TITLE = 200
MAX_TEXT = 2000
MAX_REFERENCE = 256
MAX_ITEMS = 200  # element ids, traces, preconditions … of one step, risk or checkpoint


class PlanStatus(StrEnum):
    """Where a plan version is in its review — never whether anything was executed."""

    DRAFT = "draft"  # generated or revised, not yet submitted
    NEEDS_INFORMATION = "needs_information"  # something required is missing: it cannot be reviewed as is
    READY_FOR_REVIEW = "ready_for_review"  # submitted by a person
    APPROVED = "approved"  # a person with migration.approve approved this exact version
    REJECTED = "rejected"  # a person rejected it, with feedback
    SUPERSEDED = "superseded"  # a later version of the plan replaced it
    ARCHIVED = "archived"  # kept for history, no longer in use


class StepType(StrEnum):
    PREPARE = "prepare"
    PROVISION = "provision"
    CONFIGURE = "configure"
    REPLICATE = "replicate"
    BACKFILL = "backfill"
    VERIFY = "verify"
    CUTOVER = "cutover"
    DECOMMISSION = "decommission"
    ROLLBACK = "rollback"
    MANUAL_REVIEW = "manual_review"


class TargetKind(StrEnum):
    REVISION = "revision"  # a later exact revision of the same architecture
    CANDIDATE = "candidate"  # an evolution candidate, rebuilt on its exact baseline


class TraceKind(StrEnum):
    """What a step, risk or checkpoint rests on — every generated item names at least one."""

    CHANGE = "change"  # an architecture change (element id and change)
    REQUIREMENT = "requirement"
    ASSUMPTION = "assumption"  # an explicit assumption of the request
    PATTERN = "pattern"  # a migration pattern or rule, with its version
    CONSTRAINT = "constraint"  # a constraint the request stated
    EVIDENCE = "evidence"  # a stored analysis of an engine


class DowntimeStatus(StrEnum):
    KNOWN_DOWNTIME = "known_downtime"  # the architecture and the method require it
    POTENTIAL_DOWNTIME = "potential_downtime"  # it may be required; the conditions are stated
    MODELED_ONLINE = "modeled_online"  # the modeled architecture supports an online change
    UNKNOWN = "unknown"  # not established — never read as online


class Reversibility(StrEnum):
    REVERSIBLE = "reversible"
    CONDITIONALLY_REVERSIBLE = "conditionally_reversible"  # under stated conditions
    IRREVERSIBLE = "irreversible"
    UNKNOWN = "unknown"  # not established — never read as safe


class RiskCategory(StrEnum):
    DATA_LOSS = "data_loss"
    DATA_INCONSISTENCY = "data_inconsistency"
    DOWNTIME = "downtime"
    CAPACITY_EXHAUSTION = "capacity_exhaustion"
    COMPATIBILITY_FAILURE = "compatibility_failure"
    SECURITY_REGRESSION = "security_regression"
    OBSERVABILITY_GAP = "observability_gap"
    ROLLBACK_LIMITATION = "rollback_limitation"
    DEPENDENCY_ORDERING = "dependency_ordering"
    COST_INCREASE = "cost_increase"
    OPERATIONAL_COMPLEXITY = "operational_complexity"
    IRREVERSIBLE_CHANGE = "irreversible_change"


class RiskStatus(StrEnum):
    CONFIRMED = "confirmed"  # the evidence establishes the condition
    POTENTIAL = "potential"  # it holds if a stated precondition holds
    UNKNOWN = "unknown"  # the evidence needed is missing


class CheckpointStatus(StrEnum):
    """A checkpoint's status at planning time — never proof that a migration will succeed."""

    PASS = "pass"  # noqa: S105 - a status, not a password
    FAIL = "fail"
    WARNING = "warning"
    NOT_RUN = "not_run"  # no analysis of it exists yet
    CANNOT_EVALUATE = "cannot_evaluate"
    MANUAL_VERIFICATION_REQUIRED = "manual_verification_required"


class CheckpointBasis(StrEnum):
    MODELED = "modeled"  # an engine's model of the architecture
    MANUAL = "manual"  # a person verifies it
    RUNTIME_OBSERVED = "runtime_observed"  # observed on the running system (never at planning time here)


class CompatibilityStatus(StrEnum):
    VERIFIED = "verified"  # machine-checkable evidence establishes it
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


class FindingType(StrEnum):
    """What keeps a plan from being complete or trustworthy — stated, never silently dropped."""

    UNSUPPORTED_CHANGE = "unsupported_change"  # no pattern supports this change
    MANUAL_INTERPRETATION = "manual_interpretation"  # the change needs a person to interpret it
    MISSING_INFORMATION = "missing_information"  # an input the plan needs is not stated
    MISSING_EVIDENCE = "missing_evidence"
    STALE_EVIDENCE = "stale_evidence"
    STRATEGY_NOT_SUPPORTED = "strategy_not_supported"  # a preferred strategy's prerequisites are not modeled
    INVALID_DEPENDENCY = "invalid_dependency"  # a step depends on a step that does not exist
    DEPENDENCY_CYCLE = "dependency_cycle"
    MISSING_PREREQUISITE = "missing_prerequisite"  # a transition lacks what must precede it
    NO_CHANGES = "no_changes"  # source and target do not differ in anything migration-relevant


# --- checking helpers ------------------------------------------------------------------------------


def check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidMigrationPlan(details={"fields": found})


def text(value: object, name: str, limit: int = MAX_TEXT, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    return None if isinstance(value, str) and value.strip() and len(value) <= limit else name


def code(value: object, name: str) -> str | None:
    return None if isinstance(value, str) and CODE.fullmatch(value) else name


def texts(values: object, name: str, limit: int = MAX_TEXT) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ITEMS:
        return name
    return None if all(text(v, name, limit) is None for v in values) else name


def references(values: object, name: str) -> str | None:
    return texts(values, name, MAX_REFERENCE)


def items(values: object, kind: type, name: str) -> str | None:
    ok = isinstance(values, tuple) and len(values) <= MAX_ITEMS and all(isinstance(v, kind) for v in values)
    return None if ok else name


def digest(prefix: str, *parts: Any) -> str:
    """A stable id from content: the same parts always give the same id."""
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
