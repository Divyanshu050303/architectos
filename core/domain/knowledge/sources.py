"""A knowledge source: one uploaded document, or one ArchitectOS record (an ADR, a requirement) read as
a snapshot — what it is, where its index stands, and which version is in force.

**One project.** A source belongs to exactly one project; nothing about it is visible elsewhere.

**Versions.** Each successful ingestion of new content creates a ``SourceVersion`` (numbered from 1,
with the content's checksum). The source points at the version in force (``indexed_version``):
retrieval reads only that version. An ingestion of unchanged content creates nothing; a failed one
changes nothing — the last known-good version stays in force until a replacement completes.

**Snapshots.** A record source indexes the record as it was when ingested (``RecordRef``: the
record and, for a requirement, its version). When the record changes, the source becomes ``stale``
— still retrievable, said to be out of date — until someone re-indexes it. Nothing re-indexes it
behind a person's back.

**Archived** sources are kept with their versions (for audit) but never retrieved, and never
ingested again.
"""

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from .errors import InvalidKnowledgeRequest, InvalidKnowledgeTransition
from .values import (
    CONTENT_TYPES,
    FINGERPRINT,
    MAX_NAME,
    RECORDS,
    ContentType,
    IndexStatus,
    Lifecycle,
    SourceType,
    check,
    count,
    metadata_problem,
    text,
)

MAX_LABEL = 64


def _invalid(field_name: str, reason: str) -> InvalidKnowledgeRequest:
    return InvalidKnowledgeRequest(details={"field": field_name, "reason": reason})


@dataclass(frozen=True, slots=True)
class RecordRef:
    """The ArchitectOS record a source snapshots: its kind, id, how people cite it (``ADR-3``, a
    requirement's key) and — for a versioned record — the version read."""

    kind: SourceType
    record_id: uuid.UUID
    label: str
    version: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in RECORDS:
            raise _invalid("record.kind", "not_a_record_type")
        if not isinstance(self.record_id, uuid.UUID):
            raise _invalid("record.record_id", "required")
        if text(self.label, "record.label", MAX_LABEL):
            raise _invalid("record.label", "invalid_text")
        if count(self.version, "record.version", required=False, minimum=1):
            raise _invalid("record.version", "invalid_version")
        if (self.kind is SourceType.REQUIREMENT) != (self.version is not None):
            raise _invalid("record.version", "requirements_only")  # a requirement is versioned

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "record_id": str(self.record_id),
            "label": self.label,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    id: uuid.UUID
    project_id: uuid.UUID
    type: SourceType
    name: str
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    path: str | None = None  # an uploaded document's relative name, e.g. docs/runbook.md
    record: RecordRef | None = None  # a record source's snapshot
    status: IndexStatus = IndexStatus.PENDING
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    indexed_version: int | None = None  # the version retrieval reads
    indexed_checksum: str | None = None  # that version's content checksum
    metadata: dict[str, str] = field(default_factory=dict)
    archived_at: datetime | None = None
    archived_by_user_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if text(self.name, "name", MAX_NAME):
            raise _invalid("name", "invalid_text")
        if not isinstance(self.type, SourceType):
            raise _invalid("type", "unsupported_source_type")
        if (self.type in RECORDS) != (self.record is not None):
            raise _invalid("record", "records_only")
        if self.record is not None and self.record.kind is not self.type:
            raise _invalid("record.kind", "type_mismatch")
        if (self.type in RECORDS) == (self.path is not None):
            raise _invalid("path", "uploads_only")
        check(
            [
                text(self.path, "path", 256, required=False),
                count(self.indexed_version, "indexed_version", required=False, minimum=1),
                "indexed_checksum"
                if self.indexed_checksum is not None and not FINGERPRINT.fullmatch(self.indexed_checksum)
                else None,
                "indexed_version"
                if (self.indexed_version is None) != (self.indexed_checksum is None)
                else None,
                "status"
                if (self.status in {IndexStatus.INDEXED, IndexStatus.STALE}) and self.indexed_version is None
                else None,
                "archived_at"
                if (self.lifecycle is Lifecycle.ARCHIVED) != (self.archived_at is not None)
                else None,
                metadata_problem(self.metadata, "metadata"),
            ]
        )

    @property
    def content_type(self) -> ContentType:
        return CONTENT_TYPES[self.type]

    @property
    def retrievable(self) -> bool:
        """Active, with a version in force (stale included: retrievable, said to be out of date)."""
        return self.lifecycle is Lifecycle.ACTIVE and self.indexed_version is not None

    def _active(self, to: str) -> None:
        if self.lifecycle is Lifecycle.ARCHIVED:
            raise InvalidKnowledgeTransition(details={"from": self.lifecycle.value, "to": to})

    def begin_ingestion(self, at: datetime) -> KnowledgeSource:
        self._active(IndexStatus.PROCESSING.value)
        if self.status is IndexStatus.PROCESSING:
            raise InvalidKnowledgeTransition(details={"from": self.status.value, "to": "processing"})
        return replace(self, status=IndexStatus.PROCESSING, updated_at=at)

    def indexed(self, version: int, checksum: str, at: datetime) -> KnowledgeSource:
        """A new version is in force — only after its documents and chunks were stored."""
        expected = (self.indexed_version or 0) + 1
        if version != expected:
            raise InvalidKnowledgeTransition(details={"from": str(self.indexed_version), "to": str(version)})
        return replace(
            self,
            status=IndexStatus.INDEXED,
            indexed_version=version,
            indexed_checksum=checksum,
            updated_at=at,
        )

    def unchanged(self, at: datetime) -> KnowledgeSource:
        """Read again, the same content: the version in force is current."""
        if self.indexed_version is None:
            raise InvalidKnowledgeTransition(details={"from": self.status.value, "to": "indexed"})
        return replace(self, status=IndexStatus.INDEXED, updated_at=at)

    def ingestion_failed(self, at: datetime) -> KnowledgeSource:
        """The last known-good version, if any, stays in force (and stays stale if it was)."""
        if self.indexed_version is None:
            return replace(self, status=IndexStatus.FAILED, updated_at=at)
        return replace(self, status=IndexStatus.INDEXED, updated_at=at)

    def stale(self, at: datetime) -> KnowledgeSource:
        """The snapshotted record changed since the version in force was read."""
        if self.record is None or self.indexed_version is None:
            raise InvalidKnowledgeTransition(details={"from": self.status.value, "to": "stale"})
        return replace(self, status=IndexStatus.STALE, updated_at=at)

    def with_record(self, record: RecordRef) -> KnowledgeSource:
        """The record version a re-index reads (the id and kind never change)."""
        if self.record is None or (record.kind, record.record_id) != (
            self.record.kind,
            self.record.record_id,
        ):
            raise _invalid("record", "another_record")
        return replace(self, record=record)

    def archive(self, user_id: uuid.UUID, at: datetime) -> KnowledgeSource:
        self._active(Lifecycle.ARCHIVED.value)
        if self.status is IndexStatus.PROCESSING:
            raise InvalidKnowledgeTransition(details={"from": self.status.value, "to": "archived"})
        return replace(
            self, lifecycle=Lifecycle.ARCHIVED, archived_at=at, archived_by_user_id=user_id, updated_at=at
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "project_id": str(self.project_id),
            "type": self.type.value,
            "name": self.name,
            "content_type": self.content_type.value,
            "path": self.path,
            "record": self.record.to_dict() if self.record else None,
            "status": self.status.value,
            "lifecycle": self.lifecycle.value,
            "indexed_version": self.indexed_version,
            "indexed_checksum": self.indexed_checksum,
            "metadata": dict(sorted(self.metadata.items())),
        }


@dataclass(frozen=True, slots=True)
class SourceVersion:
    """One indexed reading of a source: written once, never changed. ``versions`` names the adapter,
    normalizer and chunking strategy (with their versions) that produced its documents and chunks."""

    source_id: uuid.UUID
    number: int
    checksum: str
    ingestion_run_id: uuid.UUID
    created_at: datetime
    documents: int
    chunks: int
    versions: dict[str, int]
    record: RecordRef | None = None  # the record version read, for a record source

    def __post_init__(self) -> None:
        check(
            [
                count(self.number, "version.number", minimum=1),
                None
                if isinstance(self.checksum, str) and FINGERPRINT.fullmatch(self.checksum)
                else "checksum",
                count(self.documents, "version.documents", minimum=1),
                count(self.chunks, "version.chunks"),
                None
                if isinstance(self.versions, dict)
                and self.versions
                and all(isinstance(v, int) and v >= 1 for v in self.versions.values())
                else "version.versions",
            ]
        )
