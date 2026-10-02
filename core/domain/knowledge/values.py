"""The vocabulary of project knowledge: what a source is, where its index stands, how an ingestion
went, how a passage was found and how far it can be relied on — and the checking helpers, shared
with discovery's (the same validators), raising knowledge's own error.

Knowledge is **evidence, never fact**. A passage is what a source says, located exactly; whether it
is true is not established by retrieving it. Every passage therefore keeps how it is known, with
discovery's ``Verification``: an uploaded document, an ADR or a requirement was written by a person
(``user_provided``) — none is verified by retrieving it. Its record status (an ADR ``proposed`` or
``accepted``, a requirement ``draft`` or ``approved``) is stated with it, never collapsed into a
score.
"""

import re
from collections.abc import Iterable
from enum import StrEnum

from core.domain.discovery.values import (
    FINGERPRINT,
    KEY,
    Verification,
    code,
    count,
    digest,
    fingerprint,
    items,
    key,
    text,
    texts,
)

from .errors import InvalidKnowledgeRecord

__all__ = [
    "FINGERPRINT", "KEY", "Verification", "check", "code", "count", "digest", "fingerprint", "items",
    "key", "text", "texts",
]  # fmt: skip

MAX_NAME = 200
MAX_PASSAGE = 4000  # characters of one chunk's text
MAX_HEADING = 300
MAX_HEADING_DEPTH = 6
MAX_METADATA = 20  # entries
MAX_METADATA_VALUE = 200
METADATA_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class SourceType(StrEnum):
    """What a source is. Only these are supported: each has a tested adapter."""

    MARKDOWN = "markdown"  # an uploaded Markdown document
    TEXT = "text"  # an uploaded plain-text document
    DECISION = "decision"  # an ArchitectOS decision record (ADR), as a snapshot of one state
    REQUIREMENT = "requirement"  # an ArchitectOS requirement, as a snapshot of one version


UPLOADED = frozenset({SourceType.MARKDOWN, SourceType.TEXT})
RECORDS = frozenset({SourceType.DECISION, SourceType.REQUIREMENT})


class ContentType(StrEnum):
    MARKDOWN = "text/markdown"
    TEXT = "text/plain"
    RECORD = "application/vnd.architectos.record+json"  # a record's fields, read as fields


CONTENT_TYPES = {
    SourceType.MARKDOWN: ContentType.MARKDOWN,
    SourceType.TEXT: ContentType.TEXT,
    SourceType.DECISION: ContentType.RECORD,
    SourceType.REQUIREMENT: ContentType.RECORD,
}


class IndexStatus(StrEnum):
    """Where a source's index stands — separate from whether the source is archived."""

    PENDING = "pending"  # registered, never indexed
    PROCESSING = "processing"  # an ingestion is running
    INDEXED = "indexed"  # a version is indexed and retrievable
    FAILED = "failed"  # the last ingestion failed and no version was ever indexed
    STALE = "stale"  # indexed, but the record it snapshots has changed since


class Lifecycle(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"  # kept, never retrieved; its versions stay for audit


class IngestionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"  # a new version is indexed
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"  # indexed; something was skipped or limited
    UNCHANGED = "unchanged"  # the content is the indexed version's: nothing new was created
    FAILED = "failed"  # nothing changed: the previous indexed version (if any) stays in force


FINISHED = frozenset(
    {
        IngestionStatus.COMPLETED,
        IngestionStatus.COMPLETED_WITH_WARNINGS,
        IngestionStatus.UNCHANGED,
        IngestionStatus.FAILED,
    }
)


class Stage(StrEnum):
    """How far an ingestion got — where a failure happened."""

    VALIDATING = "validating"
    EXTRACTING = "extracting"
    NORMALIZING = "normalizing"
    CHUNKING = "chunking"
    INDEXING = "indexing"
    DONE = "done"


class Trigger(StrEnum):
    REGISTER = "register"  # the source's first ingestion
    REINDEX = "reindex"  # a person asked for the source to be read again


class RetrievalMethod(StrEnum):
    """How a passage was found. Semantic similarity is not configured: no embeddings are stored."""

    IDENTIFIER = "identifier"  # an exact identifier the passage names (ADR-3, a requirement id, a node id)
    LEXICAL = "lexical"  # full-text match of the query's terms


def check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidKnowledgeRecord(details={"fields": found})


def metadata_problem(values: object, name: str) -> str | None:
    """Short identifiers to short text: never content, never a credential (callers redact)."""
    if not isinstance(values, dict) or len(values) > MAX_METADATA:
        return name
    for k, v in values.items():
        if not isinstance(k, str) or not METADATA_KEY.fullmatch(k):
            return name
        if text(v, name, MAX_METADATA_VALUE):
            return name
    return None
