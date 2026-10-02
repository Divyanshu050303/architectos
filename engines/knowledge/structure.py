"""Normalization (rule ``knowledge-structure@1``): the structure of a document, found without changing a
word — sections under their heading path, and the segments inside them, each with its exact lines.

**Markdown.** ATX headings (``#`` to ``######``) outside fenced code open sections; the heading path
is the chain of open headings. Inside a section, segments are separated by blank lines — except a
fenced code block (```` ``` ```` or ``~~~``), which is one segment whatever it contains, up to its
closing fence (or the end). A segment's kind (``code``, ``table``, ``list``, ``paragraph``) tells
the chunker how it may be split. Setext headings (underlined with ``===``) and HTML are text: they
are kept as written, never interpreted. Front matter is text.

**Plain text** has no headings: one section, its paragraphs separated by blank lines.

**Records** are their fields: one segment per field, located by the field, without lines.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from core.domain.knowledge.values import MAX_HEADING, MAX_HEADING_DEPTH

from .adapters import RecordField

RULE = "knowledge-structure"
VERSION = 1

_HEADING = re.compile(r"^ {0,3}(?P<marks>#{1,6})[ \t]+(?P<text>.*?)[ \t]*(?:#+[ \t]*)?$")
_FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
_TABLE = re.compile(r"^\s*\|")
_LIST = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])\s+")


class SegmentKind(StrEnum):
    HEADING = "heading"  # a section's own heading line
    CODE = "code"  # split only between lines
    TABLE = "table"  # split only between rows
    LIST = "list"  # split only between lines
    PARAGRAPH = "paragraph"  # split between lines, then between words
    FIELD = "field"  # a record field


@dataclass(frozen=True, slots=True)
class Segment:
    kind: SegmentKind
    start: int  # 1-based first line (0 for a record field: no lines)
    end: int
    text: str
    field: str | None = None  # a record field's name


@dataclass(frozen=True, slots=True)
class Section:
    heading_path: tuple[str, ...]
    segments: tuple[Segment, ...]

    @property
    def has_content(self) -> bool:
        return any(s.kind is not SegmentKind.HEADING for s in self.segments)


def _kind(lines: list[str]) -> SegmentKind:
    if all(_TABLE.match(line) for line in lines):
        return SegmentKind.TABLE
    if _LIST.match(lines[0]):
        return SegmentKind.LIST
    return SegmentKind.PARAGRAPH


def _heading(text: str) -> str:
    return (text.strip() or "(untitled)")[:MAX_HEADING]


def markdown(text: str) -> tuple[Section, ...]:
    lines = text.split("\n")
    sections: list[Section] = []
    path: list[tuple[int, str]] = []  # (level, heading)
    segments: list[Segment] = []
    pending: list[int] = []  # line numbers of the open segment
    fence: str | None = None

    def close() -> None:
        if pending:
            chunk = [lines[n - 1] for n in pending]
            kind = SegmentKind.CODE if _FENCE.match(chunk[0]) else _kind(chunk)
            segments.append(Segment(kind, pending[0], pending[-1], "\n".join(chunk)))
            pending.clear()

    def open_section() -> None:
        close()
        if segments:
            sections.append(Section(tuple(h for _, h in path)[:MAX_HEADING_DEPTH], tuple(segments)))
            segments.clear()

    for number, line in enumerate(lines, start=1):
        if fence is not None:
            pending.append(number)
            closing = _FENCE.match(line)
            if (
                closing
                and closing.group("fence")[0] == fence[0]
                and len(closing.group("fence")) >= len(fence)
            ):
                fence = None
                close()
            continue
        opening = _FENCE.match(line)
        if opening:
            close()
            fence = opening.group("fence")
            pending.append(number)
            continue
        heading = _HEADING.match(line)
        if heading:
            open_section()
            level = len(heading.group("marks"))
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, _heading(heading.group("text"))))
            segments.append(Segment(SegmentKind.HEADING, number, number, line))
            continue
        if not line.strip():
            close()
            continue
        pending.append(number)
    open_section()
    return tuple(sections)


def plain(text: str) -> tuple[Section, ...]:
    lines = text.split("\n")
    segments: list[Segment] = []
    pending: list[int] = []
    for number, line in enumerate([*lines, ""], start=1):
        if line.strip():
            pending.append(number)
        elif pending:
            chunk = [lines[n - 1] for n in pending]
            segments.append(Segment(_kind(chunk), pending[0], pending[-1], "\n".join(chunk)))
            pending = []
    return (Section((), tuple(segments)),) if segments else ()


def record(fields: tuple[RecordField, ...]) -> tuple[Section, ...]:
    segments = tuple(Segment(SegmentKind.FIELD, 0, 0, f.text, f.field) for f in fields if f.text.strip())
    return (Section((), segments),) if segments else ()
