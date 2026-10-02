"""Retrieval contracts: what a caller may ask for, and what comes back — passages with citations, how
each was found, how far it can be relied on, and what the search could not cover.

**Scope is never the caller's to widen.** A query names no project or organization: the project is
the one the caller is authorized for, resolved before anything is read. Its filters (sources,
source types, identifiers) only narrow within that project.

**A result is evidence, not an answer.** Each passage is the source's own words with a citation that
says exactly where they are (only the locator parts the source supports). ``rank`` is the order of
the passages for this query — a ranking signal, not a measure of truth or relevance; no similarity
score is reported. No passage is returned to fill a requested count: fewer, or none, is an explicit
``insufficient_evidence``. A missing passage never means a statement is false or a property absent.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from core.domain.text import has_forbidden_characters

from .documents import KnowledgeChunk, Locator
from .errors import InvalidKnowledgeRequest
from .values import (
    KEY,
    MAX_NAME,
    MAX_PASSAGE,
    RetrievalMethod,
    SourceType,
    Verification,
    check,
    code,
    count,
    items,
    text,
    texts,
)

MAX_QUERY = 500
MAX_RESULTS = 20
DEFAULT_RESULTS = 10
MAX_FILTER_IDS = 50
MAX_QUERY_IDENTIFIERS = 20
SEMANTIC_NOT_CONFIGURED = (
    "Semantic similarity is not configured: passages were found by exact identifiers and terms only."
)


def _invalid(field_name: str, reason: str) -> InvalidKnowledgeRequest:
    return InvalidKnowledgeRequest(details={"field": field_name, "reason": reason})


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    text: str | None = None  # terms to find; whitespace-normalized
    identifiers: tuple[str, ...] = ()  # exact identifiers: "ADR-3", a requirement key, a node id
    source_ids: tuple[uuid.UUID, ...] = ()  # narrows to these sources of the project
    source_types: tuple[SourceType, ...] = ()
    limit: int = DEFAULT_RESULTS
    include_stale: bool = True  # stale sources are still evidence, said to be out of date

    def __post_init__(self) -> None:
        query = self.text
        if query is not None:
            if not isinstance(query, str) or has_forbidden_characters(query, {"\n", "\t", "\r"}):
                raise _invalid("text", "invalid_text")
            query = " ".join(query.split()) or None
            if query is not None and len(query) > MAX_QUERY:
                raise _invalid("text", "too_long")
            object.__setattr__(self, "text", query)
        identifiers = self.identifiers
        if not isinstance(identifiers, tuple) or len(identifiers) > MAX_QUERY_IDENTIFIERS:
            raise _invalid("identifiers", "too_many")
        if not all(isinstance(i, str) and KEY.fullmatch(i) for i in identifiers):
            raise _invalid("identifiers", "invalid_identifier")
        if query is None and not identifiers:
            raise _invalid("text", "required")  # terms or identifiers: never "everything"
        if not isinstance(self.source_ids, tuple) or len(self.source_ids) > MAX_FILTER_IDS:
            raise _invalid("source_ids", "too_many")
        if not all(isinstance(s, uuid.UUID) for s in self.source_ids):
            raise _invalid("source_ids", "invalid_id")
        if not isinstance(self.source_types, tuple) or not all(
            isinstance(t, SourceType) for t in self.source_types
        ):
            raise _invalid("source_types", "unsupported_source_type")
        limit = self.limit
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_RESULTS:
            raise _invalid("limit", "out_of_range")
        object.__setattr__(self, "identifiers", tuple(sorted(set(identifiers))))
        object.__setattr__(self, "source_ids", tuple(sorted(set(self.source_ids))))
        object.__setattr__(self, "source_types", tuple(sorted(set(self.source_types))))


@dataclass(frozen=True, slots=True)
class Candidate:
    """A passage the index proposes for a query, with what a citation needs from its source. Found by a
    prefilter (the index); the engine decides — it never trusts the prefilter for scope or match."""

    project_id: uuid.UUID
    chunk: KnowledgeChunk
    source_name: str
    source_type: SourceType
    stale: bool  # the source's snapshot is out of date
    verification: Verification
    record_status: str | None = None


@dataclass(frozen=True, slots=True)
class Scope:
    """What a search covered: retrievable sources searched after the filters, and those in scope that
    were not (never indexed or failed; stale ones when excluded). Archived sources are never searched."""

    project_id: uuid.UUID
    searched: int
    not_indexed: int = 0
    stale_excluded: int = 0


@dataclass(frozen=True, slots=True)
class Citation:
    """Where a passage is: source, the version read, document, chunk and location."""

    source_id: uuid.UUID
    source_name: str
    source_type: SourceType
    source_version: int
    document_id: str
    chunk_id: str
    locator: Locator

    def __post_init__(self) -> None:
        check(
            [
                text(self.source_name, "citation.source_name", MAX_NAME),
                None if isinstance(self.source_type, SourceType) else "citation.source_type",
                count(self.source_version, "citation.source_version", minimum=1),
                text(self.document_id, "citation.document_id", 64),
                text(self.chunk_id, "citation.chunk_id", 64),
                items((self.locator,), Locator, "citation.locator"),
            ]
        )

    @property
    def reference(self) -> str:
        """e.g. ``Runbook (v2): Failover (lines 12-30)``."""
        return f"{self.source_name} (v{self.source_version}): {self.locator.reference()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": str(self.source_id),
            "source_name": self.source_name,
            "source_type": self.source_type.value,
            "source_version": self.source_version,
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "locator": self.locator.to_dict(),
            "reference": self.reference,
        }


@dataclass(frozen=True, slots=True)
class Passage:
    citation: Citation
    text: str  # the source's words
    method: RetrievalMethod
    rank: int  # 1-based order for this query
    verification: Verification  # how it is known: retrieval verifies nothing
    record_status: str | None = None  # an ADR's or requirement's status when read
    stale: bool = False  # the snapshotted record has changed since
    matched: tuple[str, ...] = ()  # the identifiers or query terms it matched
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                text(self.text, "passage.text", MAX_PASSAGE),
                None if isinstance(self.method, RetrievalMethod) else "passage.method",
                count(self.rank, "passage.rank", minimum=1),
                None if isinstance(self.verification, Verification) else "passage.verification",
                code(self.record_status, "passage.record_status", required=False),
                texts(self.matched, "passage.matched", 128),
                texts(self.limitations, "passage.limitations"),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation": self.citation.to_dict(),
            "text": self.text,
            "method": self.method.value,
            "rank": self.rank,
            "verification": self.verification.value,
            "record_status": self.record_status,
            "stale": self.stale,
            "matched": list(self.matched),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    passages: tuple[Passage, ...]
    searched_sources: int  # retrievable sources in scope after the filters
    limitations: tuple[str, ...] = ()
    versions: dict[str, int] | None = None  # the retrieval rules' versions

    def __post_init__(self) -> None:
        ranks = [p.rank for p in self.passages]
        chunks = [p.citation.chunk_id for p in self.passages]
        check(
            [
                items(self.passages, Passage, "result.passages", MAX_RESULTS),
                "result.ranks" if ranks != list(range(1, len(ranks) + 1)) else None,
                "result.passages" if len(set(chunks)) != len(chunks) else None,  # no duplicates
                count(self.searched_sources, "result.searched_sources"),
                texts(self.limitations, "result.limitations"),
            ]
        )

    @property
    def insufficient_evidence(self) -> bool:
        """Nothing in scope supports the query — which is not evidence against it."""
        return not self.passages

    def to_dict(self) -> dict[str, Any]:
        return {
            "passages": [p.to_dict() for p in self.passages],
            "insufficient_evidence": self.insufficient_evidence,
            "searched_sources": self.searched_sources,
            "limitations": list(self.limitations),
            "versions": dict(sorted((self.versions or {}).items())),
        }
