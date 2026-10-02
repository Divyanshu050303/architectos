"""Source adapters: one per supported source type, each turning its input into a typed ``Extraction`` —
the canonical content, where it came from, its checksum, title, record status and metadata — or a
structured ``ExtractionFailed`` (codes and safe messages with the line, never the content).

- ``markdown@1`` / ``text@1`` read an uploaded document. Canonical text: a leading byte-order mark
  removed, line endings ``LF``; nothing else changes. Refused: control characters other than tab
  and newline, bidirectional overrides (they make text read differently than it is), lines over
  10,000 characters, more than 20,000 lines, and documents with nothing but whitespace. A Markdown
  title is its first level-1 heading outside code blocks; plain text has none.
- ``decision@1`` reads an ADR's fields (title, context, goals, each option, the chosen option,
  rationale, related elements); ``requirement@1`` a requirement version's (title, statement, its
  classification, its structured constraint). Each field is located by name — the record has no
  lines. A deleted requirement is not read.

Every adapter redacts secret-looking values (``knowledge-redaction@1``) before computing the
checksum, so an identical document always has the same checksum and a secret never reaches it.
Adapters only read what they are given: no file system, no network, no execution.
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field

from core.domain.decisions.entities import Decision
from core.domain.knowledge.documents import Locator
from core.domain.knowledge.ingestion import IngestionError
from core.domain.knowledge.sources import RecordRef
from core.domain.knowledge.uploads import DocumentUpload
from core.domain.knowledge.values import CONTENT_TYPES, ContentType, SourceType, Stage, fingerprint
from core.domain.requirements.entities import Requirement

from . import redaction

VERSIONS = {"markdown": 1, "text": 1, "decision": 1, "requirement": 1}
MAX_LINES = 20_000
MAX_LINE = 10_000
# Bidirectional embeddings, overrides and isolates (U+202A-U+202E, U+2066-U+2069).
BIDI = frozenset(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))
ALLOWED_CONTROLS = frozenset("\t\n")
_TITLE = re.compile(r"^#[ \t]+(?P<title>.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^[ \t]{0,3}(```|~~~)")


class ExtractionFailed(Exception):
    def __init__(self, errors: tuple[IngestionError, ...]) -> None:
        super().__init__(", ".join(e.code for e in errors))
        self.errors = errors


def _fail(
    code: str, message: str, line: int | None = None, field_name: str | None = None
) -> ExtractionFailed:
    locator = None
    if line is not None:
        locator = Locator(line_start=line, line_end=line)
    elif field_name is not None:
        locator = Locator(field=field_name)
    return ExtractionFailed((IngestionError(code, message, Stage.EXTRACTING, locator),))


@dataclass(frozen=True, slots=True)
class RecordField:
    field: str  # its name in the record: "context", "options[1]"
    label: str  # how a person reads it: "Context", "Option 2: Read replicas"
    text: str


@dataclass(frozen=True, slots=True)
class Extraction:
    type: SourceType
    reference: str  # an upload's path, or a record's label
    checksum: str  # of the canonical, redacted content
    adapter: str  # "markdown", "decision", …
    text: str | None = None  # an upload's canonical text
    fields: tuple[RecordField, ...] = ()  # a record's fields, in order
    title: str | None = None
    record: RecordRef | None = None
    record_status: str | None = None
    redactions: int = 0
    metadata: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def content_type(self) -> ContentType:
        return CONTENT_TYPES[self.type]

    @property
    def versions(self) -> dict[str, int]:
        return {self.adapter: VERSIONS[self.adapter], redaction.RULE: redaction.VERSION}


def _redaction_warning(count: int) -> tuple[str, ...]:
    if not count:
        return ()
    noun = "value was" if count == 1 else "values were"
    return (f"{count} secret-looking {noun} redacted before indexing.",)


# --- uploaded documents ----------------------------------------------------------------------------


def _canonical(content: str) -> str:
    text = content.removeprefix("﻿")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _check_characters(lines: list[str]) -> None:
    if len(lines) > MAX_LINES:
        raise _fail("too_many_lines", f"The document has more than {MAX_LINES} lines.")
    for number, line in enumerate(lines, start=1):
        if len(line) > MAX_LINE:
            raise _fail("line_too_long", f"A line is longer than {MAX_LINE} characters.", number)
        for character in line:
            if character in BIDI:
                raise _fail(
                    "bidirectional_override",
                    "A bidirectional control character makes the text read differently than it is.",
                    number,
                )
            if unicodedata.category(character) == "Cc" and character not in ALLOWED_CONTROLS:
                raise _fail("invalid_characters", "The document contains control characters.", number)


def _markdown_title(lines: list[str]) -> str | None:
    fenced = False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced and (found := _TITLE.match(line)):
            return found.group("title")[:200]
    return None


def extract_upload(upload: DocumentUpload) -> Extraction:
    text = _canonical(upload.content)
    lines = text.split("\n")
    _check_characters(lines)
    if not text.strip():
        raise _fail("nothing_to_index", "The document has no text.")
    redacted, count = redaction.redact(text)
    kind = upload.source_type
    adapter = "markdown" if kind is SourceType.MARKDOWN else "text"
    metadata = {"lines": str(len(lines))}
    if count:
        metadata["redactions"] = str(count)
    return Extraction(
        type=kind,
        reference=upload.path,
        checksum=hashlib.sha256(redacted.encode()).hexdigest(),
        adapter=adapter,
        text=redacted,
        title=_markdown_title(redacted.split("\n")) if kind is SourceType.MARKDOWN else None,
        redactions=count,
        metadata=metadata,
        warnings=_redaction_warning(count),
    )


# --- records ---------------------------------------------------------------------------------------


def _record(
    kind: SourceType, ref: RecordRef, adapter: str, status: str, title: str, raw: list[RecordField]
) -> Extraction:
    fields: list[RecordField] = []
    total = 0
    for item in raw:
        if not item.text.strip():
            continue
        text, count = redaction.redact(item.text.replace("\r\n", "\n").replace("\r", "\n"))
        total += count
        fields.append(RecordField(item.field, item.label, text))
    if not fields:
        raise _fail("nothing_to_index", "The record has no text to index.")
    content = {"status": status, "fields": [[f.field, f.label, f.text] for f in fields]}
    metadata = {"fields": str(len(fields))}
    if total:
        metadata["redactions"] = str(total)
    return Extraction(
        type=kind,
        reference=ref.label,
        checksum=fingerprint(content),
        adapter=adapter,
        fields=tuple(fields),
        title=title[:200],
        record=ref,
        record_status=status,
        redactions=total,
        metadata=metadata,
        warnings=_redaction_warning(total),
    )


def _bullets(values: tuple[str, ...] | list[str]) -> str:
    return "\n".join(f"- {v}" for v in values)


def extract_decision(decision: Decision) -> Extraction:
    label = decision.reference
    raw = [
        RecordField("title", "Title", decision.title),
        RecordField("context", "Context", decision.context),
        RecordField("goals", "Goals", _bullets(decision.goals)),
    ]
    chosen = None
    for index, option in enumerate(decision.options):
        parts = [f"{option.title} ({option.category})"]
        if option.changes:
            parts.append("Changes:\n" + _bullets(option.changes))
        parts.append(f"Validation: {option.validation}")
        if option.goals:
            parts.append("Goals:\n" + _bullets(option.goals))
        if option.consequences:
            parts.append("Consequences:\n" + _bullets([f"{d}: {v}" for d, v in option.consequences]))
        raw.append(RecordField(f"options[{index}]", f"Option {index + 1}: {option.title}", "\n".join(parts)))
        if option.candidate_id == decision.chosen_option:
            chosen = option.title
    if chosen is not None:
        raw.append(RecordField("chosen_option", "Chosen option", chosen))
    if decision.rationale:
        raw.append(RecordField("rationale", "Rationale", decision.rationale))
    if decision.related_element_ids:
        raw.append(
            RecordField("related_elements", "Related elements", ", ".join(decision.related_element_ids))
        )
    ref = RecordRef(SourceType.DECISION, decision.id, label)
    title = f"{label}: {decision.title}"
    return _record(SourceType.DECISION, ref, "decision", decision.status.value, title, raw)


def extract_requirement(requirement: Requirement) -> Extraction:
    if requirement.is_deleted:
        raise _fail("record_deleted", "The requirement was deleted.")
    content = requirement.content
    label = requirement.reference
    details = (
        f"Type: {content.type.value}\nCategory: {content.category}\nPriority: {content.priority.value}\n"
        f"Scope: {content.scope.value}"
    )
    raw = [
        RecordField("title", "Title", content.title),
        RecordField("statement", "Statement", content.statement),
        RecordField("classification", "Classification", details),
    ]
    if content.structured_data:
        constraint = json.dumps(content.structured_data, sort_keys=True, ensure_ascii=False)
        raw.append(RecordField("constraint", "Structured constraint", constraint))
    ref = RecordRef(SourceType.REQUIREMENT, requirement.id, label, requirement.version)
    title = f"{label}: {content.title}"
    return _record(SourceType.REQUIREMENT, ref, "requirement", content.status.value, title, raw)
