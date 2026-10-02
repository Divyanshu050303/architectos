"""Chunking: a document's structure cut into retrievable passages — deterministic for identical input
and configuration, versioned, and never rewriting a word.

Strategies (each ``name@version``, recorded with every chunk):

- ``sections@1`` (Markdown): passages never cross a section boundary. A section's segments — its
  heading line first — are packed in order while the passage (the source's own lines, from its first
  to its last, blank lines between included) stays within ``max_chars``. A section with nothing but
  its heading is not indexed (counted as skipped).
- ``paragraphs@1`` (plain text): the same packing, of paragraphs, in one section.
- ``fields@1`` (records): one passage per field; a field is never merged with another.

A segment larger than ``max_chars`` is split between lines (a code block, table or list only
there); a single line larger than that, between words, and a word larger than that, where the limit
falls. A split part keeps its line range — the line or lines it is in. No overlap: passages never
repeat text. ``max_chars`` is configurable between 200 and 4,000 (default 1,500) and recorded with
the version; no single size is claimed best for every source.

Identifiers (rule ``knowledge-identifiers@1``): ``ADR-<n>``, ``REQ-<n>`` and identifiers written in
backticks (`` `orders-api` ``, `` `databases/postgresql` ``) — exactly as written, never inferred.
"""

import re
import uuid
from collections import Counter
from dataclasses import dataclass

from core.domain.knowledge.documents import KnowledgeChunk, KnowledgeDocument, Locator
from core.domain.knowledge.errors import InvalidKnowledgeRequest
from core.domain.knowledge.ingestion import IngestionError
from core.domain.knowledge.values import KEY, MAX_PASSAGE, SourceType, Stage, Verification

from . import structure
from .adapters import Extraction, ExtractionFailed
from .structure import Section, Segment

STRATEGIES = {
    SourceType.MARKDOWN: ("sections", 1),
    SourceType.TEXT: ("paragraphs", 1),
    SourceType.DECISION: ("fields", 1),
    SourceType.REQUIREMENT: ("fields", 1),
}
IDENTIFIERS = ("knowledge-identifiers", 1)
MIN_CHARS = 200
DEFAULT_CHARS = 1500
MAX_CHUNKS = 5000
_RECORD_ID = re.compile(r"\b(?:ADR|REQ)-[1-9][0-9]{0,8}\b")
_CODE_SPAN = re.compile(r"`([^`\s]{1,128})`")


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    max_chars: int = DEFAULT_CHARS

    def __post_init__(self) -> None:
        size = self.max_chars
        if not isinstance(size, int) or isinstance(size, bool) or not MIN_CHARS <= size <= MAX_PASSAGE:
            raise InvalidKnowledgeRequest(details={"field": "chunking.max_chars", "reason": "out_of_range"})


@dataclass(frozen=True, slots=True)
class Chunked:
    document: KnowledgeDocument
    chunks: tuple[KnowledgeChunk, ...]
    skipped: int  # sections with nothing to index
    warnings: tuple[str, ...]
    versions: dict[str, int]


@dataclass(frozen=True, slots=True)
class _Piece:
    start: int  # 0 for record fields
    end: int
    text: str
    whole: bool  # whole lines of the source (may be packed with neighbours)
    field: str | None = None


def identifiers(text: str) -> tuple[str, ...]:
    found = set(_RECORD_ID.findall(text))
    found |= {span for span in _CODE_SPAN.findall(text) if KEY.fullmatch(span)}
    return tuple(sorted(found))[:50]


def _words(line: str, number: int, limit: int, field: str | None) -> list[_Piece]:
    pieces: list[_Piece] = []
    current = ""
    for found in re.findall(r"\S+\s*", line):
        token = found
        while len(token.rstrip()) > limit:  # a "word" longer than a passage: cut where the limit falls
            if current.strip():
                pieces.append(_Piece(number, number, current.rstrip(), False, field))
                current = ""
            pieces.append(_Piece(number, number, token[:limit], False, field))
            token = token[limit:]
        if len((current + token).rstrip()) > limit and current.strip():
            pieces.append(_Piece(number, number, current.rstrip(), False, field))
            current = ""
        current += token
    if current.strip():
        pieces.append(_Piece(number, number, current.rstrip(), False, field))
    return pieces


def _split(segment: Segment, limit: int) -> list[_Piece]:
    """A segment as pieces within ``limit``: whole when it fits, else by lines, then by words."""
    field = segment.field
    first = segment.start  # 0: a record field, without lines
    if len(segment.text) <= limit:
        return [_Piece(first, segment.end, segment.text, first > 0, field)]
    pieces: list[_Piece] = []
    group: list[str] = []
    group_start = first

    def flush() -> None:
        nonlocal group
        if "\n".join(group).strip():
            end = group_start + len(group) - 1 if first else 0
            pieces.append(_Piece(group_start, end, "\n".join(group), False, field))
        group = []

    for offset, line in enumerate(segment.text.split("\n")):
        number = first + offset if first else 0
        if len(line) > limit:
            flush()
            pieces.extend(_words(line, number, limit, field))
            group_start = number + 1 if first else 0
            continue
        if group and len("\n".join([*group, line])) > limit:
            flush()
            group_start = number
        if not group:
            group_start = number
        group.append(line)
    flush()
    return pieces


def _passages(section: Section, lines: list[str], limit: int) -> list[_Piece]:
    pieces = [p for segment in section.segments for p in _split(segment, limit)]
    if not lines:  # a record: one passage per field part, never merged
        return pieces
    packed: list[_Piece] = []
    for piece in pieces:
        last = packed[-1] if packed else None
        if last is not None and last.whole and piece.whole:
            text = "\n".join(lines[last.start - 1 : piece.end])  # the source's own lines, as written
            if len(text) <= limit:
                packed[-1] = _Piece(last.start, piece.end, text, True)
                continue
        packed.append(piece)
    return packed


def _fail(code: str, message: str) -> ExtractionFailed:
    return ExtractionFailed((IngestionError(code, message, Stage.CHUNKING),))


def chunk(
    extraction: Extraction, source_id: uuid.UUID, version: int, config: ChunkingConfig | None = None
) -> Chunked:
    """The document and passages of one source version. Pure: the same extraction, source, version
    and configuration always give the same document, passages, ids and order."""
    limit = (config or ChunkingConfig()).max_chars
    kind = extraction.type
    text = extraction.text or ""
    if kind is SourceType.MARKDOWN:
        sections, lines = structure.markdown(text), text.split("\n")
    elif kind is SourceType.TEXT:
        sections, lines = structure.plain(text), text.split("\n")
    else:
        sections, lines = structure.record(extraction.fields), []
    document = KnowledgeDocument(
        source_id, version, extraction.content_type, extraction.checksum, extraction.reference,
        extraction.title, Verification.USER_PROVIDED, extraction.record_status, dict(extraction.metadata),
    )  # fmt: skip
    strategy_name, strategy_version = STRATEGIES[kind]
    strategy = f"{strategy_name}@{strategy_version}"
    label = extraction.record.label if extraction.record else None
    chunks: list[KnowledgeChunk] = []
    seen: Counter[tuple[tuple[str, ...], str]] = Counter()
    skipped = 0
    for section in sections:
        if not section.has_content:
            skipped += 1
            continue
        for piece in _passages(section, lines, limit):
            if piece.start:
                locator = Locator(section.heading_path, piece.start, piece.end)
            else:
                locator = Locator(record=label, field=piece.field)
            same = (section.heading_path, piece.text)  # what a chunk id is made of, besides its order
            found = KnowledgeChunk(
                source_id, version, document.id, len(chunks), piece.text, locator, strategy,
                seen[same], identifiers(piece.text),
            )  # fmt: skip
            seen[same] += 1
            chunks.append(found)
            if len(chunks) > MAX_CHUNKS:
                raise _fail("too_many_chunks", f"The document would have more than {MAX_CHUNKS} passages.")
    if not chunks:
        raise _fail("nothing_to_index", "The document has headings but no text under them.")
    warnings = list(extraction.warnings)
    if skipped:
        verb = "has only a heading and was" if skipped == 1 else "have only a heading and were"
        warnings.append(f"{skipped} {'section' if skipped == 1 else 'sections'} {verb} not indexed.")
    versions = extraction.versions | {
        structure.RULE: structure.VERSION,
        strategy_name: strategy_version,
        IDENTIFIERS[0]: IDENTIFIERS[1],
        "chunk-max-chars": limit,
    }
    return Chunked(document, tuple(chunks), skipped, tuple(warnings), versions)
