"""A workflow step: one action the controller executed, recorded as the workflow's checkpoint.

- **Operation identity.** A step's ``key`` is derived from the workflow, the iteration, the action and
  its subject (a candidate, an engine): the same action on the same subject always has the same key.
  A completed key is never executed again — a retry or a resume after a crash continues after the last
  completed step instead of repeating side effects.
- **Append-only.** A step is written once, when its action ends: completed (with references to what
  it produced), skipped (not applicable, with why) or failed (with a stable code, and whether the
  failure may be retried under the same key).
- What a step used (model calls, tokens, retrievals…) is counted on the workflow, step by step.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .budget import WorkflowUsage
from .tools import TOOLS
from .values import Action, Stage, StepStatus, check, code, count, digest, text

MAX_OUTPUTS = 40
MAX_ATTEMPTS = 2  # a retryable failure is tried once more, never more


def operation_key(workflow_id: uuid.UUID, iteration: int, action: Action, subject: str = "") -> str:
    """The stable identity of one action on one subject in one iteration of one workflow."""
    return digest("wst", str(workflow_id), iteration, action.value, subject)


def _outputs(value: object) -> str | None:
    """References only (ids, counts, codes): never content."""
    if not isinstance(value, dict) or len(value) > MAX_OUTPUTS:
        return "step.outputs"
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str | int | bool | type(None)):
            return "step.outputs"
        if isinstance(v, str) and len(v) > 200:
            return "step.outputs"
    return None


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    key: str
    workflow_id: uuid.UUID
    ordinal: int  # 1, 2, … in execution order
    iteration: int  # 0 for the first candidate's stages, n for the n-th improvement round
    action: Action
    stage: Stage
    status: StepStatus
    started_at: datetime
    completed_at: datetime
    subject: str = ""  # what it acted on: a candidate id, an engine, "" for the workflow itself
    attempt: int = 1  # a retryable failure is attempted again under the same key, at most MAX_ATTEMPTS
    candidate_id: uuid.UUID | None = None
    outputs: dict[str, Any] = field(default_factory=dict)  # references to what it produced
    usage: WorkflowUsage = field(default_factory=WorkflowUsage)
    error: str | None = None  # a stable code when it failed or was skipped
    retryable: bool = False  # a failure that may be attempted again under the same key
    note: str | None = None  # why, for a person: never a provider's raw error or content

    def __post_init__(self) -> None:
        failed = self.status is StepStatus.FAILED
        spec = TOOLS.get(self.action) if isinstance(self.action, Action) else None
        check(
            [
                None if isinstance(self.action, Action) and spec is not None else "step.action",
                None if spec is None or self.stage in spec.stages else "step.stage",
                None
                if isinstance(self.action, Action)
                and self.key == operation_key(self.workflow_id, self.iteration, self.action, self.subject)
                else "step.key",
                count(self.ordinal, "step.ordinal", minimum=1),
                count(self.iteration, "step.iteration"),
                count(self.attempt, "step.attempt", minimum=1),
                "step.attempt" if isinstance(self.attempt, int) and self.attempt > MAX_ATTEMPTS else None,
                "step.retryable" if self.retryable and self.attempt >= MAX_ATTEMPTS else None,
                None if isinstance(self.status, StepStatus) else "step.status",
                text(self.subject, "step.subject", 64) if self.subject else None,
                _outputs(self.outputs),
                None if isinstance(self.usage, WorkflowUsage) else "step.usage",
                code(self.error, "step.error", required=False),
                # a completed step has no error; a failed or skipped one says why
                "step.error" if (self.status is StepStatus.COMPLETED) != (self.error is None) else None,
                "step.retryable" if self.retryable and not failed else None,
                text(self.note, "step.note", 500, required=False),
                "step.completed_at" if self.completed_at < self.started_at else None,
            ]
        )

    @property
    def done(self) -> bool:
        """Never executed again under its key: it completed, was skipped, or failed for good."""
        return self.status is not StepStatus.FAILED or not self.retryable
