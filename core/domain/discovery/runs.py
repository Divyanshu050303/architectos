"""A discovery request, a discovery run and its review.

**The request** supplies the source artifacts inline — each a relative path and its text content —
within fixed limits (count, size per artifact, total size). Paths are names, never locations: an
absolute path, a ``..`` segment, a backslash or a control character is refused. The content is read
by the adapters and never stored: a run keeps each artifact's hash and size only. An optional
source type applies to every artifact (otherwise each is detected), and an optional baseline names
the architecture revision a later comparison is made against.

**A run** moves ``pending`` → ``running`` → ``completed`` / ``completed_with_warnings`` /
``failed``, or ``cancelled`` before it finishes. It records who asked and when, and its result or
error.

**Review** is per item and recorded: a person accepts, rejects or ignores each candidate entity and
relationship, and resolves an ambiguous mapping by choosing one of its candidates. Decisions are
history (the latest per item applies). Accepting a proposal is recorded with the architecture and
revision it created — created through the architecture workflow, never written here.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from .errors import InvalidDiscoveryRequest, InvalidDiscoveryTransition
from .results import DiscoveryResult
from .values import FINISHED, Decision, MappingStatus, RunStatus, SourceType

MAX_ARTIFACTS = 50
MAX_ARTIFACT_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_PATH = 256
MAX_LABEL = 200
MAX_COMMENT = 2000
SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._ -]{0,127}$")

S = RunStatus
TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    S.PENDING: frozenset({S.RUNNING, S.FAILED, S.CANCELLED}),
    S.RUNNING: frozenset({S.COMPLETED, S.COMPLETED_WITH_WARNINGS, S.FAILED, S.CANCELLED}),
    S.COMPLETED: frozenset(),
    S.COMPLETED_WITH_WARNINGS: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}
REVIEWABLE = frozenset({S.COMPLETED, S.COMPLETED_WITH_WARNINGS})


def _invalid(field: str, reason: str, **extra: Any) -> InvalidDiscoveryRequest:
    return InvalidDiscoveryRequest(details={"field": field, "reason": reason, **extra})


def _text(value: object, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def safe_path(path: object) -> bool:
    """A relative name made of plain segments: no absolute path, no ``.``/``..``, no backslash."""
    if not isinstance(path, str) or not path or len(path) > MAX_PATH:
        return False
    return all(
        SEGMENT.fullmatch(segment) and segment not in {".", ".."} and not segment.endswith(" ")
        for segment in path.split("/")
    )


@dataclass(frozen=True, slots=True)
class ArtifactInput:
    path: str
    content: str

    def __post_init__(self) -> None:
        if not safe_path(self.path):
            raise _invalid("artifacts.path", "unsafe_path")
        if not isinstance(self.content, str):
            raise _invalid("artifacts.content", "not_text", artifact=self.path)
        if self.size_bytes > MAX_ARTIFACT_BYTES:
            raise _invalid("artifacts.content", "too_large", artifact=self.path, limit=MAX_ARTIFACT_BYTES)

    @property
    def size_bytes(self) -> int:
        return len(self.content.encode("utf-8", errors="surrogatepass"))

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8", errors="surrogatepass")).hexdigest()


@dataclass(frozen=True, slots=True)
class Baseline:
    """The architecture revision a later comparison is made against."""

    architecture_id: uuid.UUID
    revision_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.architecture_id, uuid.UUID):
            raise _invalid("baseline.architecture_id", "required")
        number = self.revision_number
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise _invalid("baseline.revision", "invalid_revision")

    def to_dict(self) -> dict[str, Any]:
        return {"architecture_id": str(self.architecture_id), "revision_number": self.revision_number}


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    artifacts: tuple[ArtifactInput, ...]
    source_type: SourceType | None = None  # None: detected for each artifact
    baseline: Baseline | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        artifacts = self.artifacts
        if not isinstance(artifacts, tuple) or not artifacts:
            raise _invalid("artifacts", "required")
        if len(artifacts) > MAX_ARTIFACTS:
            raise _invalid("artifacts", "too_many", limit=MAX_ARTIFACTS)
        if not all(isinstance(a, ArtifactInput) for a in artifacts):
            raise _invalid("artifacts", "invalid")
        if len({a.path for a in artifacts}) != len(artifacts):
            raise _invalid("artifacts.path", "duplicate")
        if sum(a.size_bytes for a in artifacts) > MAX_TOTAL_BYTES:
            raise _invalid("artifacts", "too_large", limit=MAX_TOTAL_BYTES)
        if self.source_type is not None and not isinstance(self.source_type, SourceType):
            raise _invalid("source_type", "unsupported")
        if self.baseline is not None and not isinstance(self.baseline, Baseline):
            raise _invalid("baseline", "invalid")
        if self.label is not None and not _text(self.label, MAX_LABEL):
            raise _invalid("label", "invalid_text")
        object.__setattr__(self, "artifacts", tuple(sorted(artifacts, key=lambda a: a.path)))


class SubjectType(StrEnum):
    ENTITY = "entity"
    RELATIONSHIP = "relationship"


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """A person's decision about one candidate: who, when, what — and for an entity, optionally the
    catalog component they chose."""

    subject_type: SubjectType
    subject: str  # an entity key or a relationship id
    decision: Decision
    user_id: uuid.UUID
    at: datetime
    component_id: str | None = None
    comment: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.subject_type, SubjectType):
            raise _invalid("subject_type", "invalid")
        if not _text(self.subject, MAX_PATH):
            raise _invalid("subject", "invalid")
        if not isinstance(self.decision, Decision) or self.decision is Decision.PENDING:
            raise _invalid("decision", "invalid")  # a decision is made, never pending
        chosen = self.subject_type is SubjectType.ENTITY and self.decision is Decision.ACCEPTED
        if self.component_id is not None and not chosen:
            raise _invalid("component_id", "only_for_an_accepted_entity")
        if self.comment is not None and not _text(self.comment, MAX_COMMENT):
            raise _invalid("comment", "invalid_text")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_type": self.subject_type.value,
            "subject": self.subject,
            "decision": self.decision.value,
            "user_id": str(self.user_id),
            "at": self.at.isoformat(),
            "component_id": self.component_id,
            "comment": self.comment,
        }


@dataclass(frozen=True, slots=True)
class RunError:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class Acceptance:
    """A person accepted the reviewed proposal: the architecture and revision it created."""

    architecture_id: uuid.UUID
    revision_number: int
    content_hash: str
    user_id: uuid.UUID
    at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture_id": str(self.architecture_id),
            "revision_number": self.revision_number,
            "content_hash": self.content_hash,
            "user_id": str(self.user_id),
            "at": self.at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryRun:
    id: uuid.UUID
    project_id: uuid.UUID
    status: RunStatus
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    source_type: SourceType | None = None  # as requested; None: detected per artifact
    baseline: Baseline | None = None
    label: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: DiscoveryResult | None = None
    error: RunError | None = None
    decisions: tuple[ReviewDecision, ...] = ()
    acceptances: tuple[Acceptance, ...] = ()

    def _move(self, to: RunStatus) -> None:
        if to not in TRANSITIONS[self.status]:
            raise InvalidDiscoveryTransition(details={"from": self.status.value, "to": to.value})

    def start(self, at: datetime) -> DiscoveryRun:
        self._move(S.RUNNING)
        return replace(self, status=S.RUNNING, started_at=at)

    def finish(self, result: DiscoveryResult, at: datetime) -> DiscoveryRun:
        """Completed — with warnings when anything was unsupported, ambiguous, unresolved or invalid."""
        to = S.COMPLETED_WITH_WARNINGS if result.has_warnings else S.COMPLETED
        self._move(to)
        return replace(self, status=to, result=result, completed_at=at)

    def fail(self, error: RunError, at: datetime) -> DiscoveryRun:
        self._move(S.FAILED)
        return replace(self, status=S.FAILED, error=error, completed_at=at)

    def cancel(self, at: datetime) -> DiscoveryRun:
        self._move(S.CANCELLED)
        return replace(self, status=S.CANCELLED, completed_at=at)

    @property
    def finished(self) -> bool:
        return self.status in FINISHED

    # --- review ----------------------------------------------------------------------------------

    def decide(self, decision: ReviewDecision) -> DiscoveryRun:
        """A person's decision about one candidate of a finished, successful run."""
        if self.status not in REVIEWABLE or self.result is None:
            raise InvalidDiscoveryTransition(details={"from": self.status.value, "to": "reviewed"})
        result = self.result
        if decision.subject_type is SubjectType.ENTITY:
            entity = next((e for e in result.entities if e.key == decision.subject), None)
            if entity is None:
                raise _invalid("subject", "not_in_this_run")
            mapping = entity.mapping
            ambiguous = mapping.status is MappingStatus.AMBIGUOUS
            if (
                ambiguous
                and decision.component_id is not None
                and decision.component_id not in mapping.candidates
            ):
                raise _invalid("component_id", "not_a_candidate")  # resolved among its candidates only
        elif not any(r.id == decision.subject for r in result.relationships):
            raise _invalid("subject", "not_in_this_run")
        return replace(self, decisions=(*self.decisions, decision))

    def decision_for(self, subject: str) -> ReviewDecision | None:
        """The latest decision about ``subject`` (None: still pending)."""
        return next((d for d in reversed(self.decisions) if d.subject == subject), None)

    def accepted(self, acceptance: Acceptance) -> DiscoveryRun:
        if self.status not in REVIEWABLE:
            raise InvalidDiscoveryTransition(details={"from": self.status.value, "to": "accepted"})
        return replace(self, acceptances=(*self.acceptances, acceptance))
