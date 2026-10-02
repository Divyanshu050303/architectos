"""Documents and chunks: what an ingestion read from a source version, and the retrievable passages it
was cut into — each keeping exactly where it came from.

**Locators say only what the source supports.** An uploaded document is located by its heading path
and line range; a record by its label and field (``ADR-3`` ``context``, ``options[1]``). No page,
no section, no line is invented for a source that has none: a missing locator part stays ``None``.

**Stable identities.** A document's id is derived from its source and content checksum; a chunk's
from its source, the chunking strategy, its heading path, its text's checksum and its occurrence
among identical passages. Unchanged content therefore keeps its ids across readings, and a changed
passage gets a new one — the version it belongs to is always stated alongside.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from .values import (
    FINGERPRINT,
    MAX_HEADING,
    MAX_HEADING_DEPTH,
    MAX_NAME,
    MAX_PASSAGE,
    ContentType,
    Verification,
    check,
    code,
    count,
    digest,
    items,
    metadata_problem,
    text,
    texts,
)

MAX_FIELD = 64
MAX_IDENTIFIERS = 50
MAX_IDENTIFIER = 128
STRATEGY = re.compile(r"^[a-z][a-z0-9_-]{0,47}@[1-9][0-9]{0,3}$")


def _checksum(value: object, name: str) -> str | None:
    return None if isinstance(value, str) and FINGERPRINT.fullmatch(value) else name


@dataclass(frozen=True, slots=True)
class Locator:
    """Where a passage is in its source. ``heading_path`` and lines for documents; ``record`` and
    ``field`` for records. Only the parts the source supports are set."""

    heading_path: tuple[str, ...] = ()
    line_start: int | None = None  # 1-based, inclusive
    line_end: int | None = None
    record: str | None = None  # how the record is cited, e.g. "ADR-3"
    field: str | None = None  # the record field, e.g. "context", "options[1]"

    def __post_init__(self) -> None:
        start, end = self.line_start, self.line_end
        check(
            [
                texts(self.heading_path, "locator.heading_path", MAX_HEADING),
                "locator.heading_path" if len(self.heading_path) > MAX_HEADING_DEPTH else None,
                count(start, "locator.line_start", required=False, minimum=1),
                count(end, "locator.line_end", required=False, minimum=1),
                "locator.lines"
                if (start is None) != (end is None) or (start is not None and end is not None and end < start)
                else None,
                text(self.record, "locator.record", 64, required=False),
                text(self.field, "locator.field", MAX_FIELD, required=False),
                "locator" if self.is_empty else None,
            ]
        )

    @property
    def is_empty(self) -> bool:
        return not (self.heading_path or self.line_start or self.record or self.field)

    def reference(self) -> str:
        """A human-readable citation of the location, e.g. ``Runbook > Failover (lines 12-30)``."""
        parts: list[str] = []
        if self.record:
            parts.append(self.record + (f" {self.field}" if self.field else ""))
        elif self.field:
            parts.append(self.field)
        if self.heading_path:
            parts.append(" > ".join(self.heading_path))
        if self.line_start is not None:
            lines = (
                f"line {self.line_start}"
                if self.line_start == self.line_end
                else f"lines {self.line_start}-{self.line_end}"
            )
            parts.append(f"({lines})")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "heading_path": list(self.heading_path),
            "line_start": self.line_start,
            "line_end": self.line_end,
            "record": self.record,
            "field": self.field,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """One logical document of a source version: its title (when the source gives one), checksum,
    how it is known and — for a record — the record's status when read."""

    source_id: uuid.UUID
    source_version: int
    content_type: ContentType
    checksum: str  # of the normalized content
    reference: str  # the source's own identity: an upload's path, or a record's label
    title: str | None = None
    verification: Verification = Verification.USER_PROVIDED
    record_status: str | None = None  # e.g. "accepted", "approved": stated, never interpreted
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        check(
            [
                count(self.source_version, "document.source_version", minimum=1),
                None if isinstance(self.content_type, ContentType) else "document.content_type",
                _checksum(self.checksum, "document.checksum"),
                text(self.reference, "document.reference", 256),
                text(self.title, "document.title", MAX_NAME, required=False),
                None if isinstance(self.verification, Verification) else "document.verification",
                code(self.record_status, "document.record_status", required=False),
                metadata_problem(self.metadata, "document.metadata"),
            ]
        )

    @property
    def id(self) -> str:
        return digest("kdo", str(self.source_id), self.checksum)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": str(self.source_id),
            "source_version": self.source_version,
            "content_type": self.content_type.value,
            "checksum": self.checksum,
            "reference": self.reference,
            "title": self.title,
            "verification": self.verification.value,
            "record_status": self.record_status,
            "metadata": dict(sorted(self.metadata.items())),
        }


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """A retrievable passage. ``text`` is the source's words, normalized only in whitespace and line
    endings — never rewritten: numbers, units, identifiers and code are kept as written.
    ``identifiers`` are the exact identifiers the passage names (``ADR-3``, a requirement key, a
    component or node id), found by a documented rule — never inferred from meaning."""

    source_id: uuid.UUID
    source_version: int
    document_id: str
    sequence: int  # 0-based order within the document
    text: str
    locator: Locator
    strategy: str  # the chunking strategy and its version, e.g. "markdown-sections@1"
    occurrence: int = 0  # among passages with the same heading path and text
    identifiers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                count(self.source_version, "chunk.source_version", minimum=1),
                text(self.document_id, "chunk.document_id", 64),
                count(self.sequence, "chunk.sequence"),
                text(self.text, "chunk.text", MAX_PASSAGE),
                items((self.locator,), Locator, "chunk.locator"),
                None
                if isinstance(self.strategy, str) and STRATEGY.fullmatch(self.strategy)
                else "chunk.strategy",
                count(self.occurrence, "chunk.occurrence"),
                texts(self.identifiers, "chunk.identifiers", MAX_IDENTIFIER),
                "chunk.identifiers" if len(self.identifiers) > MAX_IDENTIFIERS else None,
            ]
        )
        object.__setattr__(self, "identifiers", tuple(sorted(set(self.identifiers))))

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @property
    def id(self) -> str:
        """Stable while the passage is: same source, strategy, place and words."""
        return digest(
            "kch", str(self.source_id), self.strategy, list(self.locator.heading_path), self.checksum,
            self.occurrence,
        )  # fmt: skip

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": str(self.source_id),
            "source_version": self.source_version,
            "document_id": self.document_id,
            "sequence": self.sequence,
            "text": self.text,
            "checksum": self.checksum,
            "locator": self.locator.to_dict(),
            "strategy": self.strategy,
            "occurrence": self.occurrence,
            "identifiers": list(self.identifiers),
        }
