import uuid
from collections.abc import Mapping
from typing import Protocol

from .ingestion import IngestionRun
from .ports import Indexed
from .retrieval import Candidate, RetrievalQuery, Scope
from .sources import KnowledgeSource
from .values import IndexStatus, Lifecycle, SourceType


class KnowledgeRepository(Protocol):
    """Knowledge sources (identity fixed; index state, record version read and lifecycle change),
    their versions, documents and passages (written once, in full), and ingestion runs (written once)
    — every read scoped by project."""

    async def add_source(self, source: KnowledgeSource) -> KnowledgeSource:
        """Raises ``KnowledgeSourceExists`` when the path or record is already an active source."""
        ...

    async def save_source(self, source: KnowledgeSource) -> KnowledgeSource:
        """Index status, version in force, record version read, lifecycle (nothing else changes)."""
        ...

    async def get_source(
        self, project_id: uuid.UUID, source_id: uuid.UUID, *, for_update: bool = False
    ) -> KnowledgeSource | None: ...

    async def find_source(
        self, project_id: uuid.UUID, *, path: str | None = None, record_id: uuid.UUID | None = None
    ) -> KnowledgeSource | None:
        """The active source of this path or record, if any."""
        ...

    async def list_sources(
        self,
        project_id: uuid.UUID,
        *,
        status: IndexStatus | None = None,
        source_type: SourceType | None = None,
        lifecycle: Lifecycle | None = Lifecycle.ACTIVE,
        after: uuid.UUID | None = None,  # the last id of the previous page
        limit: int = 50,
    ) -> list[KnowledgeSource]:
        """In registration order (ids are time-ordered)."""
        ...

    async def add_run(self, run: IngestionRun) -> IngestionRun: ...

    async def get_run(
        self, project_id: uuid.UUID, source_id: uuid.UUID, run_id: uuid.UUID
    ) -> IngestionRun | None: ...

    async def list_runs(
        self, project_id: uuid.UUID, source_id: uuid.UUID, *, after: uuid.UUID | None = None, limit: int = 50
    ) -> list[IngestionRun]:
        """Newest first."""
        ...

    async def add_version(
        self, project_id: uuid.UUID, indexed: Indexed, terms: Mapping[str, tuple[str, ...]]
    ) -> None:
        """The version, its document and every passage (with its terms, by chunk id) — in full."""
        ...

    async def scope(self, project_id: uuid.UUID, query: RetrievalQuery) -> Scope:
        """What a search of ``query``'s sources covers: retrievable sources, those not indexed and stale
        ones excluded — active sources of the project only."""
        ...

    async def candidates(
        self, project_id: uuid.UUID, query: RetrievalQuery, terms: tuple[str, ...], limit: int
    ) -> list[Candidate]:
        """Passages of the indexed versions of the project's active sources (within the query's
        filters) naming one of the query's identifiers or holding one of ``terms`` — most shared terms
        first, at most ``limit``. A prefilter: the engine decides."""
        ...

    async def chunk(
        self, project_id: uuid.UUID, source_id: uuid.UUID, chunk_id: str, version: int | None = None
    ) -> Candidate | None:
        """A passage of an active source: of the version in force, or of ``version``."""
        ...
