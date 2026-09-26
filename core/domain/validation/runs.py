"""A validation run: one execution of a rule set against one architecture revision.

Lifecycle: ``pending`` → ``running`` → ``completed`` or ``failed`` (``pending`` may also fail
directly). ``completed`` and ``failed`` are final. A completed run may contain findings: an
architecture that breaks rules is a successful validation. ``failed`` means the engine could not
produce a trustworthy result (an invalid request context, a rule that crashed under the fail-run
policy, a storage problem), recorded with a safe error code and message.

Runs reference the revision (number and content hash) they validated; they never copy the
architecture.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from .errors import InvalidRunTransition
from .results import Limitation, RequirementResult, RuleFailure, RuleSet, Summary, ValidationResult


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset({RunStatus.COMPLETED, RunStatus.FAILED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class RunError:
    """Why a run failed: a stable code and a sentence safe to show (never internals)."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationRun:
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    revision_number: int
    revision_content_hash: str
    profile: str
    status: RunStatus
    requested_by_user_id: uuid.UUID | None
    requested_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: ValidationResult | None = None
    error: RunError | None = None

    @property
    def summary(self) -> Summary | None:
        return self.result.summary if self.result is not None else None

    def _move(self, to: RunStatus) -> None:
        if to not in TRANSITIONS[self.status]:
            raise InvalidRunTransition(details={"from": self.status.value, "to": to.value})

    def start(self, at: datetime) -> ValidationRun:
        self._move(RunStatus.RUNNING)
        return replace(self, status=RunStatus.RUNNING, started_at=at)

    def complete(self, result: ValidationResult, at: datetime) -> ValidationRun:
        self._move(RunStatus.COMPLETED)
        return replace(self, status=RunStatus.COMPLETED, completed_at=at, result=result)

    def fail(self, error: RunError, at: datetime) -> ValidationRun:
        self._move(RunStatus.FAILED)
        return replace(self, status=RunStatus.FAILED, completed_at=at, error=error)


@dataclass(frozen=True, slots=True)
class RunInputs:
    """What a run was given besides the revision, recorded with it so a result can be explained
    later (the project's policy and requirements may change afterwards)."""

    config: Mapping[str, Any] = field(default_factory=dict)  # ValidationConfig.to_dict()
    policy: Mapping[str, Any] | None = None  # the policy in force, None when it constrained nothing
    requirements: tuple[tuple[str, int, str], ...] = ()  # (id, version, status) of each one given

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": dict(self.config),
            "policy": dict(self.policy) if self.policy is not None else None,
            "requirements": [list(r) for r in self.requirements],
        }


@dataclass(frozen=True, slots=True)
class RunReport:
    """A stored run as read back: everything but its findings, which are read page by page.
    ``run.result`` is None here; the summary and the rest are stored with the run."""

    run: ValidationRun
    inputs: RunInputs
    rule_set: RuleSet | None = None
    context_fingerprint: str | None = None
    result_fingerprint: str | None = None
    summary: Summary | None = None
    requirement_results: tuple[RequirementResult, ...] = ()
    failures: tuple[RuleFailure, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    @classmethod
    def of(cls, run: ValidationRun, inputs: RunInputs) -> RunReport:
        """The report of a run just executed (its result still attached)."""
        result = run.result
        if result is None:
            return cls(replace(run, result=None), inputs)
        return cls(
            replace(run, result=None),
            inputs,
            result.rule_set,
            result.context_fingerprint,
            result.fingerprint,
            result.summary,
            result.requirement_results,
            result.failures,
            result.limitations,
        )
