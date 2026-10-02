"""An ingestion run: one attempt to read a source and index what it says — when, by whom, how far it
got, what it counted, which adapter, normalizer and chunking strategy (and versions) it used, and
how it ended.

It ends exactly one way:

- ``completed`` / ``completed_with_warnings``: a new source version is indexed (its number stated);
- ``unchanged``: the content's checksum is the indexed version's — nothing new was created;
- ``failed``: nothing changed — the previous indexed version, if any, stays in force. Errors are
  structured codes with a safe message and, where known, the location: never the content itself.

``retry_of`` names the failed run a retry repeats; a retry is an ordinary run.
"""

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from .documents import Locator
from .errors import InvalidKnowledgeTransition
from .values import (
    FINGERPRINT,
    FINISHED,
    IngestionStatus,
    Stage,
    Trigger,
    check,
    code,
    count,
    items,
    text,
)

MAX_ERRORS = 50
MAX_WARNINGS = 50
S = IngestionStatus
MOVES: dict[IngestionStatus, frozenset[IngestionStatus]] = {
    S.PENDING: frozenset({S.RUNNING, S.FAILED}),
    S.RUNNING: frozenset({S.COMPLETED, S.COMPLETED_WITH_WARNINGS, S.UNCHANGED, S.FAILED}),
}


@dataclass(frozen=True, slots=True)
class IngestionError:
    """``code`` is stable (``invalid_encoding``, ``too_large``, ``nothing_to_index``…); ``message`` is
    written for a person and never quotes the content."""

    code: str
    message: str
    stage: Stage
    locator: Locator | None = None

    def __post_init__(self) -> None:
        check(
            [
                code(self.code, "error.code"),
                text(self.message, "error.message", 500),
                None if isinstance(self.stage, Stage) else "error.stage",
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "stage": self.stage.value,
            "locator": self.locator.to_dict() if self.locator else None,
        }


@dataclass(frozen=True, slots=True)
class Counts:
    documents: int = 0  # documents read
    chunks: int = 0  # passages indexed
    skipped: int = 0  # parts not indexed, with a warning (e.g. an empty section)
    failed: int = 0  # parts that could not be read

    def __post_init__(self) -> None:
        check(count(getattr(self, n), f"counts.{n}") for n in ("documents", "chunks", "skipped", "failed"))

    def to_dict(self) -> dict[str, int]:
        return {
            "documents": self.documents,
            "chunks": self.chunks,
            "skipped": self.skipped,
            "failed": self.failed,
        }


@dataclass(frozen=True, slots=True)
class IngestionRun:
    id: uuid.UUID
    project_id: uuid.UUID
    source_id: uuid.UUID
    trigger: Trigger
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    status: IngestionStatus = IngestionStatus.PENDING
    stage: Stage = Stage.VALIDATING
    checksum: str | None = None  # of the content read, once known
    counts: Counts = field(default_factory=Counts)
    versions: dict[str, int] = field(default_factory=dict)  # adapter, normalizer, chunking strategy
    indexed_version: int | None = None  # the version this run created
    warnings: tuple[str, ...] = ()
    errors: tuple[IngestionError, ...] = ()
    retry_of: uuid.UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        indexed = self.status in {S.COMPLETED, S.COMPLETED_WITH_WARNINGS}
        check(
            [
                None if isinstance(self.trigger, Trigger) else "run.trigger",
                None if self.checksum is None or FINGERPRINT.fullmatch(self.checksum) else "run.checksum",
                "run.indexed_version" if indexed != (self.indexed_version is not None) else None,
                "run.errors" if (self.status is S.FAILED) != bool(self.errors) else None,
                items(self.errors, IngestionError, "run.errors", MAX_ERRORS),
                "run.warnings"
                if not isinstance(self.warnings, tuple) or len(self.warnings) > MAX_WARNINGS
                else None,
                "run.warnings" if self.status is S.COMPLETED_WITH_WARNINGS and not self.warnings else None,
                "run.completed_at" if (self.status in FINISHED) != (self.completed_at is not None) else None,
            ]
        )

    def _move(self, to: IngestionStatus) -> None:
        if to not in MOVES.get(self.status, frozenset()):
            raise InvalidKnowledgeTransition(details={"from": self.status.value, "to": to.value})

    def start(self, at: datetime, versions: dict[str, int]) -> IngestionRun:
        self._move(S.RUNNING)
        return replace(self, status=S.RUNNING, started_at=at, versions=dict(versions))

    def at_stage(self, stage: Stage, checksum: str | None = None) -> IngestionRun:
        return replace(self, stage=stage, checksum=checksum or self.checksum)

    def complete(
        self, version: int, counts: Counts, at: datetime, warnings: tuple[str, ...] = ()
    ) -> IngestionRun:
        status = S.COMPLETED_WITH_WARNINGS if warnings else S.COMPLETED
        self._move(status)
        return replace(
            self, status=status, stage=Stage.DONE, indexed_version=version, counts=counts,
            warnings=warnings, completed_at=at,
        )  # fmt: skip

    def unchanged(self, counts: Counts, at: datetime) -> IngestionRun:
        self._move(S.UNCHANGED)
        return replace(self, status=S.UNCHANGED, stage=Stage.DONE, counts=counts, completed_at=at)

    def fail(
        self, errors: tuple[IngestionError, ...], at: datetime, counts: Counts | None = None
    ) -> IngestionRun:
        """At the stage where it stopped; nothing is indexed."""
        self._move(S.FAILED)
        return replace(self, status=S.FAILED, errors=errors, counts=counts or self.counts, completed_at=at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "source_id": str(self.source_id),
            "trigger": self.trigger.value,
            "status": self.status.value,
            "stage": self.stage.value,
            "checksum": self.checksum,
            "counts": self.counts.to_dict(),
            "versions": dict(sorted(self.versions.items())),
            "indexed_version": self.indexed_version,
            "warnings": list(self.warnings),
            "errors": [e.to_dict() for e in self.errors],
            "retry_of": str(self.retry_of) if self.retry_of else None,
        }
