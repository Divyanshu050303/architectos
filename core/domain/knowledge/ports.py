"""What the knowledge service asks of the knowledge engine: read exactly one input for a source and say
how the ingestion ended — and whether a record snapshot is out of date. The engine is pure: it reads
what it is given, stores nothing and fetches nothing; the service authorizes, loads and stores."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from core.domain.decisions.entities import Decision
from core.domain.requirements.entities import Requirement

from .documents import KnowledgeChunk, KnowledgeDocument
from .errors import InvalidKnowledgeRequest
from .ingestion import IngestionRun
from .retrieval import Candidate, RetrievalQuery, RetrievalResult, Scope
from .sources import KnowledgeSource, SourceVersion
from .uploads import DocumentUpload
from .values import SourceType


@dataclass(frozen=True, slots=True)
class IngestionInput:
    """Exactly one of: an uploaded document, the decision or the requirement version a source snapshots."""

    upload: DocumentUpload | None = None
    decision: Decision | None = None
    requirement: Requirement | None = None

    def __post_init__(self) -> None:
        given = [x for x in (self.upload, self.decision, self.requirement) if x is not None]
        if len(given) != 1:
            raise InvalidKnowledgeRequest(details={"field": "content", "reason": "exactly_one_input"})

    @property
    def type(self) -> SourceType:
        if self.upload is not None:
            return self.upload.source_type
        return SourceType.DECISION if self.decision is not None else SourceType.REQUIREMENT


@dataclass(frozen=True, slots=True)
class Indexed:
    """A new version of a source: written once, in full, before the source points at it."""

    version: SourceVersion
    document: KnowledgeDocument
    chunks: tuple[KnowledgeChunk, ...]


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    source: KnowledgeSource  # the source after the attempt (never processing)
    run: IngestionRun  # ended: completed, completed_with_warnings, unchanged or failed
    indexed: Indexed | None = None  # only when a new version was created


class KnowledgeEngine(Protocol):
    def ingest(
        self, source: KnowledgeSource, content: IngestionInput, run: IngestionRun, at: datetime
    ) -> IngestionOutcome:
        """Read ``content`` for ``source`` as ``run`` (pending). Never raises for what the content says:
        a failure is an outcome. Raises ``InvalidKnowledgeRequest`` for an input that does not belong to
        the source, and ``InvalidKnowledgeTransition`` for a source that cannot be ingested now."""
        ...

    def snapshot_changed(self, source: KnowledgeSource, content: IngestionInput) -> bool:
        """Whether the record a source snapshots now reads differently from the indexed version."""
        ...

    def versions(self, source_type: SourceType) -> dict[str, int]: ...

    def terms(self, text: str) -> tuple[str, ...]:
        """The terms a passage or query is matched by — stored with each passage for the index."""
        ...

    def retrieve(
        self, query: RetrievalQuery, candidates: Iterable[Candidate], scope: Scope
    ) -> RetrievalResult:
        """The passages that support ``query`` among ``candidates``, cited and ordered; never outside
        ``scope``'s project, never padded."""
        ...


class KnowledgeRetriever(Protocol):
    """The boundary every consumer of project knowledge uses — the requirements engine, a future
    architecture agent, an AI diff: authorized retrieval returning evidence with citations, never
    tables, embeddings or persistence models. Retrieved text is untrusted data: a consumer passing it
    to a language model keeps it apart from its instructions."""

    async def retrieve(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, query: RetrievalQuery
    ) -> RetrievalResult: ...
