"""An uploaded document, as a person sends it: a relative name, its text inline, and optionally its type.

Refused before anything is stored (``invalid_knowledge_request``): an unsafe path (absolute, ``..``,
backslashes — discovery's rule), content beyond the size limit, or a type the name contradicts or
that is not supported. What the content says is judged later, by the adapter, as part of a stored
ingestion run. Nothing in it is ever executed, rendered, expanded, fetched or followed: a link is
text, front matter is text, a code block is text.
"""

from dataclasses import dataclass, field

from core.domain.discovery.runs import safe_path

from .errors import InvalidKnowledgeRequest
from .values import UPLOADED, SourceType

MAX_DOCUMENT_BYTES = 512 * 1024  # UTF-8 encoded
EXTENSIONS = {
    SourceType.MARKDOWN: (".md", ".markdown"),
    SourceType.TEXT: (".txt", ".text"),
}


def _invalid(field_name: str, reason: str) -> InvalidKnowledgeRequest:
    return InvalidKnowledgeRequest(details={"field": field_name, "reason": reason})


def type_of(path: str) -> SourceType | None:
    """The supported type a name's extension states, if any."""
    lowered = path.lower()
    return next((t for t, endings in EXTENSIONS.items() if lowered.endswith(endings)), None)


@dataclass(frozen=True, slots=True)
class DocumentUpload:
    path: str
    content: str
    type: SourceType | None = None  # None: from the name's extension
    source_type: SourceType = field(init=False)  # the type in force

    def __post_init__(self) -> None:
        if not safe_path(self.path):
            raise _invalid("path", "unsafe_path")
        if not isinstance(self.content, str):
            raise _invalid("content", "required")
        try:
            size = len(self.content.encode("utf-8"))
        except UnicodeEncodeError:
            raise _invalid("content", "invalid_encoding") from None  # lone surrogates: not text
        if size > MAX_DOCUMENT_BYTES:
            raise _invalid("content", "too_large")
        stated = type_of(self.path)
        chosen = self.type or stated
        if chosen is None or chosen not in UPLOADED:
            raise _invalid("type", "unsupported_source_type")
        if stated is not None and stated is not chosen:
            raise _invalid("type", "contradicts_path")
        object.__setattr__(self, "source_type", chosen)
