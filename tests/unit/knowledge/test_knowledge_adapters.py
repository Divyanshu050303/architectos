"""Source adapters and safe parsing (Knowledge/RAG Engine, phase 2): uploads refused before anything is
stored when the name, size or type is wrong; content read without being rewritten, executed or
followed; hostile characters refused with the line, never the content; secrets redacted before
anything is indexed; ADRs and requirement versions read field by field with their status; equal
content, equal checksum."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from core.domain.decisions.entities import Decision, DecisionOption, DecisionStatus
from core.domain.knowledge.errors import InvalidKnowledgeRequest
from core.domain.knowledge.uploads import MAX_DOCUMENT_BYTES, DocumentUpload
from core.domain.knowledge.values import ContentType, SourceType, Stage
from engines.knowledge.adapters import (
    MAX_LINE,
    MAX_LINES,
    ExtractionFailed,
    extract_decision,
    extract_requirement,
    extract_upload,
)
from engines.knowledge.redaction import redact
from tests.unit.requirements.test_planning_input import requirement

AT = datetime(2026, 10, 2, tzinfo=UTC)
BOM, NUL, ZWJ = chr(0xFEFF), chr(0), chr(0x200D)
RLO, LRI = chr(0x202E), chr(0x2066)  # right-to-left override, left-to-right isolate
LONE_SURROGATE = chr(0xD800)
RUNBOOK = """\
# Orders runbook

Orders run on `orders-api` (ADR-3, REQ-12).

## Failover

1. Promote the replica within 30 s.
2. Keep `max_connections = 200` and 1.5 GiB per node.

```bash
# not a title: inside a code block
psql -c "select 1"
```
"""


def upload(
    content: str = RUNBOOK, path: str = "docs/runbook.md", kind: SourceType | None = None
) -> DocumentUpload:
    return DocumentUpload(path, content, kind)


def refused(content: str) -> ExtractionFailed:
    with pytest.raises(ExtractionFailed) as failed:
        extract_upload(upload(content))
    return failed.value


def test_uploads_are_refused_before_anything_is_stored() -> None:
    cases: list[tuple[dict[str, object], str]] = [
        ({"path": "../etc/passwd.md"}, "unsafe_path"),
        ({"path": "/abs/runbook.md"}, "unsafe_path"),
        ({"path": "docs\\runbook.md"}, "unsafe_path"),
        ({"content": "x" * (MAX_DOCUMENT_BYTES + 1)}, "too_large"),
        ({"content": LONE_SURROGATE}, "invalid_encoding"),  # a lone surrogate is not text
        ({"path": "slides.pdf"}, "unsupported_source_type"),
        ({"path": "page.html"}, "unsupported_source_type"),
        (
            {"path": "notes", "kind": SourceType.DECISION},
            "unsupported_source_type",
        ),  # records are not uploads
        ({"path": "notes"}, "unsupported_source_type"),  # no extension and no type: not guessed
        ({"path": "runbook.md", "kind": SourceType.TEXT}, "contradicts_path"),
    ]
    for changes, reason in cases:
        with pytest.raises(InvalidKnowledgeRequest) as invalid:
            upload(**changes)  # type: ignore[arg-type]
        assert invalid.value.details["reason"] == reason, changes
    assert upload(path="NOTES.TXT").source_type is SourceType.TEXT
    assert upload(path="notes", kind=SourceType.MARKDOWN).source_type is SourceType.MARKDOWN


def test_a_markdown_document_is_read_as_written() -> None:
    read = extract_upload(upload())
    assert (read.type, read.content_type, read.reference, read.title) == (
        SourceType.MARKDOWN, ContentType.MARKDOWN, "docs/runbook.md", "Orders runbook",
    )  # fmt: skip
    assert read.text == RUNBOOK  # nothing rewritten: numbers, units, identifiers, code
    assert read.metadata == {"lines": str(RUNBOOK.count("\n") + 1)}
    assert (read.redactions, read.warnings) == (0, ())
    assert read.versions == {"markdown": 1, "knowledge-redaction": 1}
    plain = extract_upload(upload(RUNBOOK, "runbook.txt"))
    assert (plain.type, plain.title) == (SourceType.TEXT, None)  # plain text has no headings
    fenced = extract_upload(upload("```\n# not a title\n```\n\n# Title\n"))
    assert fenced.title == "Title"


def test_equal_content_has_an_equal_checksum() -> None:
    first = extract_upload(upload())
    assert first.checksum == extract_upload(upload()).checksum
    windows = BOM + RUNBOOK.replace("\n", "\r\n")
    again = extract_upload(upload(windows))
    assert (again.checksum, again.text) == (first.checksum, RUNBOOK)  # BOM and CRLF are not content
    assert first.checksum != extract_upload(upload(RUNBOOK.replace("30 s", "60 s"))).checksum


def test_hostile_or_empty_content_fails_with_the_line_never_the_content() -> None:
    cases = {
        "invalid_characters": (f"line one\nbad {NUL} byte\n", 2),
        "bidirectional_override": (f"ok\n\nadmin{RLO}{LRI} check\n", 3),
        "line_too_long": ("x" * (MAX_LINE + 1), 1),
    }
    for code, (content, line) in cases.items():
        error = refused(content).errors[0]
        assert (error.code, error.stage) == (code, Stage.EXTRACTING)
        assert error.locator is not None
        assert error.locator.line_start == line
        assert "byte" not in error.message
        assert "admin" not in error.message
    assert refused("\n" * MAX_LINES).errors[0].code == "too_many_lines"
    for empty in ("", "   \n\t\n"):
        assert refused(empty).errors[0].code == "nothing_to_index"
    emoji = extract_upload(upload(f"# Team\n\nOn call: {chr(0x1F469)}{ZWJ}{chr(0x1F4BB)} ops\n"))
    assert ZWJ in (emoji.text or "")  # joiners in ordinary text are kept


SECRETS = """\
db_password: hunter2
API_KEY = "sk-live-123"
token='abc.def'
DATABASE_URL=postgres://app:s3cr3t@db:5432/orders
curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig" https://api
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA
abcdef
-----END RSA PRIVATE KEY-----
inline -----BEGIN PRIVATE KEY-----AAAA-----END PRIVATE KEY-----
password: ${DB_PASSWORD}
api_key: <your key>
Replicas: 3
"""


def test_secrets_are_redacted_before_anything_is_indexed() -> None:
    text, count = redact(SECRETS)
    hidden = (
        "hunter2",
        "sk-live-123",
        "abc.def",
        "s3cr3t",
        "eyJhbGciOiJIUzI1NiJ9",
        "MIIEowIBAAKCAQEA",
        "AAAA",
    )
    for secret in hidden:
        assert secret not in text, secret
    assert text.count("\n") == SECRETS.count("\n")  # lines keep their numbers
    assert "postgres://app:[redacted]@db:5432/orders" in text
    assert "-----BEGIN RSA PRIVATE KEY-----" in text  # the markers stay: where a key was is evidence
    assert "${DB_PASSWORD}" in text  # placeholders name a variable, not a value
    assert "<your key>" in text
    assert "Replicas: 3" in text
    assert count >= 8
    read = extract_upload(upload("# Config\n\n" + SECRETS))
    assert "hunter2" not in repr(read)
    assert read.metadata["redactions"] == str(read.redactions)
    assert read.warnings == (f"{read.redactions} secret-looking values were redacted before indexing.",)
    harmless = "Rotate keys every 90 days.\nThe token bucket allows 100 rps."
    assert redact(harmless) == (harmless, 0)


def option(candidate: str, title: str, *changes: str) -> DecisionOption:
    return DecisionOption(
        candidate, title, "scaling", changes, "valid", ("latency",), (("cost", "increase"),)
    )


def adr(**changes: object) -> Decision:
    options = (
        option("evo_a", "Read replicas", "db.replicas = 3"),
        option("evo_b", "Cache", "api.db_password = hunter2"),
    )
    decision = Decision(
        uuid.UUID(int=30), uuid.UUID(int=1), uuid.UUID(int=2), 3, "Scale order reads",
        DecisionStatus.PROPOSED, "Checkout reads saturate the primary at 2,000 rps.", options, None, AT,
        goals=("latency",), related_element_ids=("db", "api"),
    )  # fmt: skip
    return replace(decision, **changes)  # type: ignore[arg-type]


def test_an_adr_is_read_field_by_field_with_its_status() -> None:
    read = extract_decision(adr())
    assert (read.type, read.reference, read.title, read.record_status) == (
        SourceType.DECISION, "ADR-3", "ADR-3: Scale order reads", "proposed",
    )  # fmt: skip
    assert read.record is not None
    assert (read.record.label, read.record.version) == ("ADR-3", None)
    fields = {f.field: f for f in read.fields}
    assert list(fields) == ["title", "context", "goals", "options[0]", "options[1]", "related_elements"]
    assert fields["context"].text == "Checkout reads saturate the primary at 2,000 rps."
    assert fields["options[0]"].label == "Option 1: Read replicas"
    assert "hunter2" not in repr(read)  # a secret in a proposed change is redacted
    assert read.redactions == 1
    accepted = adr(status=DecisionStatus.ACCEPTED, chosen_option="evo_a", rationale="Cheaper than a cache.")
    decided = extract_decision(accepted)
    assert [f.field for f in decided.fields][-3:] == ["chosen_option", "rationale", "related_elements"]
    assert {f.field: f.text for f in decided.fields}["chosen_option"] == "Read replicas"
    assert decided.checksum != read.checksum  # a status change is a content change
    assert extract_decision(adr()).checksum == read.checksum


def test_a_requirement_version_is_read_with_its_classification() -> None:
    found = requirement(12, version=4)
    read = extract_requirement(found)
    assert (read.reference, read.record_status, read.title) == (
        "REQ-12",
        "active",
        "REQ-12: Checkout latency",
    )
    assert read.record is not None
    assert read.record.version == 4
    fields = {f.field: f.text for f in read.fields}
    assert fields["statement"] == "Checkout p95 latency stays under 1.5 s."
    assert "Priority: critical" in fields["classification"]
    assert '"value": "1.5"' in fields["constraint"]  # the structured constraint, as stored
    assert read.versions == {"requirement": 1, "knowledge-redaction": 1}
    with pytest.raises(ExtractionFailed) as deleted:
        extract_requirement(replace(found, deleted_at=AT))
    assert deleted.value.errors[0].code == "record_deleted"
