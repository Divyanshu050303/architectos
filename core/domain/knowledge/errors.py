from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidKnowledgeRequest(DomainError):
    """``details`` = {"field", "reason"}: a source, content or query that cannot be accepted."""

    code = "invalid_knowledge_request"
    message = "The knowledge request is invalid."


class InvalidKnowledgeRecord(InvalidEngineResult):
    """A document, chunk, run or retrieval result with malformed parts (a pipeline bug, or a corrupted
    record). ``details`` = {"fields": [...]}."""

    code = "invalid_knowledge_record"
    message = "A knowledge record is malformed."


class KnowledgeSourceNotFound(DomainError):
    """No such source in this project — also when it belongs to another project or tenant."""

    code = "knowledge_source_not_found"
    message = "Knowledge source not found."


class KnowledgeChunkNotFound(DomainError):
    """No such passage in an indexed source of this project — also when it belongs to another project
    or tenant, or to a version that is no longer indexed."""

    code = "knowledge_chunk_not_found"
    message = "Knowledge passage not found."


class IngestionRunNotFound(DomainError):
    code = "ingestion_run_not_found"
    message = "Ingestion run not found."


class InvalidKnowledgeTransition(DomainError):
    """``details`` = {"from", "to"}: a source or ingestion run cannot move to that state now (e.g.
    re-indexing an archived source)."""

    code = "invalid_knowledge_transition"
    message = "The knowledge source or ingestion run cannot move to that state now."
