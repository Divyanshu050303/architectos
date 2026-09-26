"""The Architecture IR documentation's examples are real: each is read by the IR reader, and the
revision example's diff is recomputed from the code."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.architecture_ir.commands import ChangeReplicas, apply_commands
from core.architecture_ir.diff import diff
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.serialization import from_dict

DOC = (Path(__file__).resolve().parents[3] / "docs" / "architecture" / "architecture-ir.md").read_text()
BLOCK = re.compile(r"```json\n(.*?)\n```", re.S)
EXAMPLE = re.compile(r"<!-- ir-example -->\n```json\n(.*?)\n```", re.S)
TOPICS = 19


def examples() -> list[dict[str, Any]]:
    return [json.loads(block) for block in EXAMPLE.findall(DOC)]


def test_every_ir_example_is_a_valid_architecture() -> None:
    documents = examples()
    assert len(documents) == 3
    names = [from_dict(document).name for document in documents]
    assert names == ["Orders", "Orders with events", "Production (discovered)"]


def test_the_revision_example_is_what_the_code_computes() -> None:
    [documented] = [json.loads(b) for b in BLOCK.findall(DOC) if '"summary"' in b]
    first = from_dict(examples()[0])
    edit = Provenance(
        ProvenanceSource.USER_EDIT,
        actor="user:0199a7c2-0000-7000-8000-000000000001",
        recorded_at=datetime(2026, 9, 26, 9, 0, tzinfo=UTC),
    )
    computed = diff(first, apply_commands(first, [ChangeReplicas("api", 6)], provenance=edit)).to_dict()
    assert computed == documented


def test_every_topic_of_the_specification_is_covered() -> None:
    numbered = re.findall(r"^## (\d+)\. ", DOC, re.M)
    assert [int(n) for n in numbered] == list(range(1, TOPICS + 1))
