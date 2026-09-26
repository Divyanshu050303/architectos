"""A capacity analysis: one execution of the capacity models against one architecture revision
and one workload profile.

The **request** names the exact revision (architecture id and revision number), the workload
profile (stored with the analysis as a snapshot), an optional selection of models with their
parameters, and analysis-level assumptions. Nothing in it is filled in by default.

Lifecycle: ``pending`` → ``running`` → a final status. The final status is what the result
established (``completed``, ``partial``, ``insufficient_input``, ``unsupported``) or ``failed`` when
the engine could not produce a result at all (with a safe error code and message). Final statuses
are final. Analyses reference the revision (number and content hash); they never copy it.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from .errors import InvalidAnalysisTransition, InvalidWorkload
from .results import MODEL_ID, AnalysisStatus, CapacityResult, Summary
from .workload import MAX_ASSUMPTIONS, WorkloadAssumption, WorkloadProfile

MAX_SELECTED_MODELS = 50
MAX_LABEL_LENGTH = 100
MAX_ENTRY_ID = 128
PENDING, RUNNING = "pending", "running"


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    architecture_id: uuid.UUID
    revision_number: int
    workload: WorkloadProfile
    models: tuple[str, ...] | None = None  # None: every applicable model
    parameters: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # by model id
    assumptions: tuple[WorkloadAssumption, ...] = ()
    label: str | None = None
    entries: tuple[str, ...] | None = None  # nodes where the workload arrives; None: the clients

    def __post_init__(self) -> None:
        if not isinstance(self.architecture_id, uuid.UUID):
            raise InvalidWorkload(details={"field": "architecture_id", "reason": "invalid_reference"})
        if (
            isinstance(self.revision_number, bool)
            or not isinstance(self.revision_number, int)
            or self.revision_number < 1
        ):
            raise InvalidWorkload(details={"field": "revision_number", "reason": "not_a_positive_count"})
        if not isinstance(self.workload, WorkloadProfile):
            raise InvalidWorkload(details={"field": "workload", "reason": "required"})
        if self.models is not None:
            if len(self.models) > MAX_SELECTED_MODELS:
                raise InvalidWorkload(details={"field": "models", "reason": "too_many"})
            if not all(isinstance(m, str) and MODEL_ID.fullmatch(m) for m in self.models):
                raise InvalidWorkload(details={"field": "models", "reason": "invalid_model_id"})
            object.__setattr__(self, "models", tuple(sorted(set(self.models))))
        if len(self.assumptions) > MAX_ASSUMPTIONS:
            raise InvalidWorkload(details={"field": "assumptions", "reason": "too_many"})
        keys = [a.key for a in (*self.assumptions, *self.workload.assumptions)]
        if len(keys) != len(set(keys)):
            raise InvalidWorkload(details={"field": "assumptions", "reason": "duplicate_key"})
        object.__setattr__(self, "assumptions", tuple(sorted(self.assumptions, key=lambda a: a.key)))
        if self.entries is not None:
            if not 0 < len(self.entries) <= MAX_SELECTED_MODELS or not all(
                isinstance(e, str) and 0 < len(e) <= MAX_ENTRY_ID for e in self.entries
            ):
                raise InvalidWorkload(details={"field": "entries", "reason": "invalid_entries"})
            object.__setattr__(self, "entries", tuple(sorted(set(self.entries))))
        if self.label is not None and (
            not isinstance(self.label, str) or not self.label.strip() or len(self.label) > MAX_LABEL_LENGTH
        ):
            raise InvalidWorkload(details={"field": "label", "reason": "invalid_text"})

    def inputs(self) -> dict[str, Any]:
        """Everything the result depends on besides the revision content, canonically (what the
        context fingerprint hashes and what is stored with the analysis)."""
        return {
            "architecture_id": str(self.architecture_id),
            "revision_number": self.revision_number,
            "workload": self.workload.to_dict(),
            "models": list(self.models) if self.models is not None else None,
            "parameters": {m: dict(sorted(p.items())) for m, p in sorted(self.parameters.items())},
            "assumptions": [a.to_dict() for a in self.assumptions],
            "entries": list(self.entries) if self.entries is not None else None,
        }


_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({RUNNING, AnalysisStatus.FAILED.value}),
    RUNNING: frozenset(s.value for s in AnalysisStatus),
    **{s.value: frozenset() for s in AnalysisStatus},
}


@dataclass(frozen=True, slots=True)
class AnalysisError:
    """Why an analysis failed: a stable code and a sentence safe to show (never internals)."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class CapacityAnalysis:
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    revision_number: int
    revision_content_hash: str
    status: str  # "pending", "running", or an AnalysisStatus value
    requested_by_user_id: uuid.UUID | None
    requested_at: datetime
    label: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: CapacityResult | None = None
    error: AnalysisError | None = None

    @property
    def finished(self) -> bool:
        return self.status not in (PENDING, RUNNING)

    @property
    def summary(self) -> Summary | None:
        return self.result.summary if self.result is not None else None

    def _move(self, to: str) -> None:
        if to not in _TRANSITIONS[self.status]:
            raise InvalidAnalysisTransition(details={"from": self.status, "to": to})

    def start(self, at: datetime) -> CapacityAnalysis:
        self._move(RUNNING)
        return replace(self, status=RUNNING, started_at=at)

    def finish(self, result: CapacityResult, at: datetime) -> CapacityAnalysis:
        """The final status is what the result established."""
        self._move(result.status.value)
        return replace(self, status=result.status.value, completed_at=at, result=result)

    def fail(self, error: AnalysisError, at: datetime) -> CapacityAnalysis:
        self._move(AnalysisStatus.FAILED.value)
        return replace(self, status=AnalysisStatus.FAILED.value, completed_at=at, error=error)
