"""Normalization and chunking (Knowledge/RAG Engine, phase 3): structure found without changing a word;
passages that never cross a section, keep their heading path and exact lines, stay within the
configured size, split code, tables and lists only between lines, never overlap, and keep their ids
while their words do; deterministic for identical input and configuration."""

import uuid
from datetime import UTC, datetime

import pytest

from core.domain.decisions.entities import Decision, DecisionStatus
from core.domain.knowledge.errors import InvalidKnowledgeRequest
from core.domain.knowledge.uploads import DocumentUpload
from core.domain.knowledge.values import Stage
from engines.knowledge.adapters import ExtractionFailed, extract_decision, extract_requirement, extract_upload
from engines.knowledge.chunking import MIN_CHARS, Chunked, ChunkingConfig, chunk
from engines.knowledge.structure import SegmentKind, markdown
from tests.unit.requirements.test_planning_input import requirement

SOURCE = uuid.UUID(int=3)
AT = datetime(2026, 10, 2, tzinfo=UTC)
RUNBOOK = """\
Preamble: read before an incident.

# Orders runbook

Orders run on `orders-api` and `databases/postgresql` (ADR-3, REQ-12).

## Failover

1. Promote the replica within 30 s.
2. Keep `max_connections = 200`.

| Step | Owner |
|------|-------|
| Promote | on-call |

## Restore

```bash
pg_restore --jobs 4 backup.dump

# a comment, not a heading
```

## Empty

## Failover

Second failover section: a repeated heading.
"""


def chunked(content: str = RUNBOOK, path: str = "docs/runbook.md", max_chars: int = 1500) -> Chunked:
    return chunk(extract_upload(DocumentUpload(path, content)), SOURCE, 1, ChunkingConfig(max_chars))


def assert_source_words(result: Chunked, content: str) -> None:
    """Every passage is the source's own text at its stated lines: whole lines, or a part of them."""
    lines = content.split("\n")
    for found in result.chunks:
        located = found.locator
        assert located.line_start is not None
        assert located.line_end is not None
        source = "\n".join(lines[located.line_start - 1 : located.line_end])
        assert found.text == source or found.text in source, found.locator


def test_markdown_structure_is_found_without_changing_a_word() -> None:
    sections = markdown(RUNBOOK)
    paths = [s.heading_path for s in sections]
    assert paths == [
        (), ("Orders runbook",), ("Orders runbook", "Failover"), ("Orders runbook", "Restore"),
        ("Orders runbook", "Empty"), ("Orders runbook", "Failover"),
    ]  # fmt: skip
    restore = [x.kind for x in sections[3].segments]
    assert restore == [SegmentKind.HEADING, SegmentKind.CODE]  # "# a comment" stays code
    failover = [x.kind for x in sections[2].segments]
    assert SegmentKind.TABLE in failover
    assert SegmentKind.LIST in failover
    for section in sections:
        for segment in section.segments:
            assert segment.text == "\n".join(RUNBOOK.split("\n")[segment.start - 1 : segment.end])


def test_passages_follow_sections_and_keep_their_place() -> None:
    result = chunked()
    by_path = {c.locator.heading_path: c for c in result.chunks}
    failover = next(c for c in result.chunks if c.locator.heading_path == ("Orders runbook", "Failover"))
    assert failover.text.startswith("## Failover\n\n1. Promote the replica within 30 s.")
    assert "| Promote | on-call |" in failover.text  # the table stays with its section
    assert (failover.locator.line_start, failover.locator.line_end) == (7, 14)
    assert by_path[()].text == "Preamble: read before an incident."
    assert "pg_restore --jobs 4 backup.dump" in by_path[("Orders runbook", "Restore")].text
    assert not any("Second failover" in c.text and "Promote" in c.text for c in result.chunks)
    assert (result.skipped, result.warnings) == (1, ("1 section has only a heading and was not indexed.",))
    assert [c.sequence for c in result.chunks] == list(range(len(result.chunks)))
    assert_source_words(result, RUNBOOK)
    assert result.chunks[1].identifiers == ("ADR-3", "REQ-12", "databases/postgresql", "orders-api")
    assert result.versions == {
        "markdown": 1, "knowledge-redaction": 1, "knowledge-structure": 1, "sections": 1,
        "knowledge-identifiers": 1, "chunk-max-chars": 1500,
    }  # fmt: skip


def test_repeated_headings_and_duplicate_passages_get_distinct_ids() -> None:
    twice = "# Notes\n\nSame words.\n\n# Notes\n\nSame words.\n"
    result = chunked(twice, max_chars=MIN_CHARS)
    ids = [c.id for c in result.chunks]
    assert len(ids) == len(set(ids)) == 2
    assert [c.occurrence for c in result.chunks] == [0, 1]
    assert [c.locator.line_start for c in result.chunks] == [1, 5]


def test_large_content_is_split_between_lines_then_words_within_the_limit() -> None:
    code = "```python\n" + "\n".join(f"value_{n} = {n}  # line {n}" for n in range(200)) + "\n```\n"
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| row {n} | {n * 2} |" for n in range(150))
    prose = " ".join(f"word{n}" for n in range(900))  # one line longer than any passage
    content = f"# Big\n\n{code}\n{table}\n\n{prose}\n"
    result = chunked(content, max_chars=500)
    assert all(len(c.text) <= 500 for c in result.chunks)
    assert_source_words(result, content)
    code_parts = [c for c in result.chunks if "value_" in c.text]
    assert len(code_parts) > 1
    assert all(not c.text.startswith(" ") for c in code_parts)  # split between lines, never mid-line
    rows = [c for c in result.chunks if "| row" in c.text]
    assert all(line.startswith("|") for c in rows for line in c.text.split("\n"))  # rows stay whole
    prose_line = content.split("\n").index(prose) + 1
    words = [c for c in result.chunks if c.text.startswith("word")]
    assert len(words) > 1
    assert {(c.locator.line_start, c.locator.line_end) for c in words} == {(prose_line, prose_line)}
    assert " ".join(c.text for c in words) == prose  # nothing lost, nothing repeated: no overlap
    huge = chunked("x" * 1200, "blob.txt", max_chars=500)
    assert [len(c.text) for c in huge.chunks] == [500, 500, 200]


def test_short_unicode_and_plain_text_documents() -> None:
    short = chunked("Ok.", "a.txt")
    assert [(c.text, c.locator.line_start, c.strategy) for c in short.chunks] == [("Ok.", 1, "paragraphs@1")]
    unicode = "# Équipe\n\nLatence p95 ≤ 150 ms — 東京 リージョン.\n"
    found = chunked(unicode).chunks[0]
    assert found.locator.heading_path == ("Équipe",)
    assert "≤ 150 ms" in found.text
    plain = "First paragraph.\n\nSecond paragraph\nwith two lines.\n"
    result = chunked(plain, "notes.txt")
    assert [c.text for c in result.chunks] == ["First paragraph.\n\nSecond paragraph\nwith two lines."]
    assert result.chunks[0].locator.heading_path == ()  # plain text has no headings


def test_a_document_of_headings_alone_has_nothing_to_index() -> None:
    with pytest.raises(ExtractionFailed) as failed:
        chunked("# One\n\n## Two\n")
    assert (failed.value.errors[0].code, failed.value.errors[0].stage) == ("nothing_to_index", Stage.CHUNKING)


def test_chunking_is_deterministic_and_ids_follow_the_words() -> None:
    first, again = chunked(), chunked()
    assert [c.to_dict() for c in first.chunks] == [c.to_dict() for c in again.chunks]
    edited = RUNBOOK.replace("within 30 s", "within 60 s")
    changed = chunked(edited)
    before = {c.locator.heading_path: c.id for c in first.chunks}
    after = {c.locator.heading_path: c.id for c in changed.chunks}
    restore = ("Orders runbook", "Restore")
    assert before[restore] == after[restore]  # untouched section: same id
    assert next(c.id for c in first.chunks if "30 s" in c.text) not in after.values()  # edited: new id
    version_two = chunk(extract_upload(DocumentUpload("docs/runbook.md", RUNBOOK)), SOURCE, 2)
    assert [c.id for c in version_two.chunks] == [c.id for c in first.chunks]  # same words, same ids
    assert first.document.id == version_two.document.id
    smaller = chunked(max_chars=200)
    assert smaller.versions["chunk-max-chars"] == 200  # the configuration is part of the record
    for size in (MIN_CHARS - 1, 4001, True):
        with pytest.raises(InvalidKnowledgeRequest):
            ChunkingConfig(size)


def test_records_are_one_passage_per_field() -> None:
    read = extract_requirement(requirement(12, version=4))
    result = chunk(read, SOURCE, 1)
    assert [(c.locator.record, c.locator.field) for c in result.chunks] == [
        ("REQ-12", "title"), ("REQ-12", "statement"), ("REQ-12", "classification"), ("REQ-12", "constraint"),
    ]  # fmt: skip
    assert all(c.locator.line_start is None for c in result.chunks)  # a record has no lines
    assert {c.strategy for c in result.chunks} == {"fields@1"}
    assert result.document.record_status == "active"
    long_context = "Checkout reads saturate the primary. " * 120
    decision = Decision(
        uuid.UUID(int=30), uuid.UUID(int=1), uuid.UUID(int=2), 3, "Scale reads", DecisionStatus.PROPOSED,
        long_context, (), None, AT,
    )  # fmt: skip
    parts = [c for c in chunk(extract_decision(decision), SOURCE, 1).chunks if c.locator.field == "context"]
    assert len(parts) > 1
    assert all(len(c.text) <= 1500 for c in parts)
    assert {c.locator.reference() for c in parts} == {"ADR-3 context"}


def test_an_upload_becomes_one_document_with_its_metadata() -> None:
    result = chunked()
    document = result.document
    assert (document.reference, document.title, document.source_version) == (
        "docs/runbook.md", "Orders runbook", 1,
    )  # fmt: skip
    assert all(c.document_id == document.id for c in result.chunks)
    assert document.metadata == {"lines": str(RUNBOOK.count("\n") + 1)}
    assert {c.source_id for c in result.chunks} == {SOURCE}
