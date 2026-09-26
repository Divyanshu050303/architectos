"""Architecture revisions: immutable, numbered snapshots of one architecture's content.

Revision n+1 is created from revision n (its parent) and a new IR; nothing is ever overwritten.
A revision records who or what produced it, why, and a deterministic summary of what changed;
its content hash identifies the exact state, so a simulation, a validation run or a decision can
cite precisely the architecture it was made against.

Two versions are kept apart (see core/architecture_ir/versioning.py): the IR *schema* version of
the snapshot (the format) and the revision *number* (the content).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from core.architecture_ir.diff import ArchitectureDiff, diff
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.architecture_ir.values import clean_block, text_problems

from .errors import ArchitectureUnchanged, InvalidRevision

MAX_REASON_LENGTH = 500
MAX_SUMMARY_LENGTH = 500


class RevisionSource(StrEnum):
    USER = "user"  # edited by a person
    AI = "ai"  # an AI proposal a person approved
    DISCOVERY = "discovery"  # discovered from running infrastructure
    IMPORT = "import"  # imported from a file or another tool
    SYSTEM = "system"  # produced by ArchitectOS itself (e.g. the Architecture Engine)


@dataclass(frozen=True, slots=True)
class NewRevision:
    """A revision about to be stored (the repository assigns its id and time)."""

    architecture_id: uuid.UUID
    number: int
    parent_number: int | None
    ir: ArchitectureIR
    content_hash: str
    source: RevisionSource
    summary: str
    reason: str | None
    created_by_user_id: uuid.UUID | None
    requirement_set_id: uuid.UUID | None = None  # the requirement set it was designed against, if any

    @property
    def ir_schema_version(self) -> int:
        return self.ir.schema_version


@dataclass(frozen=True, slots=True)
class ArchitectureRevision:
    id: uuid.UUID
    architecture_id: uuid.UUID
    number: int
    parent_number: int | None
    ir: ArchitectureIR
    content_hash: str
    source: RevisionSource
    summary: str
    reason: str | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    requirement_set_id: uuid.UUID | None = None

    @property
    def ir_schema_version(self) -> int:
        return self.ir.schema_version

    @property
    def label(self) -> str:
        return f"v{self.number}"


def _reason(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    reason = clean_block(raw)
    problems = text_problems(reason, "reason", MAX_REASON_LENGTH, required=True, block=True)
    if problems:
        raise InvalidRevision(problems[0].message, details={"field": "reason", "reason": problems[0].rule})
    return reason


def _summary(ir: ArchitectureIR) -> str:
    nodes, connections = len(ir.nodes), len(ir.connections)
    return (
        f"Created with {nodes} node{'s' if nodes != 1 else ''} "
        f"and {connections} connection{'s' if connections != 1 else ''}."
    )


def first_revision(
    architecture_id: uuid.UUID,
    ir: ArchitectureIR,
    *,
    source: RevisionSource,
    created_by_user_id: uuid.UUID | None,
    reason: str | None = None,
    requirement_set_id: uuid.UUID | None = None,
) -> NewRevision:
    return NewRevision(
        architecture_id=architecture_id,
        number=1,
        parent_number=None,
        ir=ir,
        content_hash=content_hash(ir),
        source=source,
        summary=_summary(ir),
        reason=_reason(reason),
        created_by_user_id=created_by_user_id,
        requirement_set_id=requirement_set_id,
    )


def next_revision(
    parent: ArchitectureRevision,
    ir: ArchitectureIR,
    *,
    source: RevisionSource,
    created_by_user_id: uuid.UUID | None,
    reason: str | None = None,
    requirement_set_id: uuid.UUID | None = None,
) -> tuple[NewRevision, ArchitectureDiff]:
    """The revision after ``parent`` holding ``ir``, and what changed. An edit that changes
    nothing creates no revision (``ArchitectureUnchanged``). Without a new ``requirement_set_id``,
    the parent's carries over."""
    changes = diff(parent.ir, ir)
    if changes.is_empty:
        raise ArchitectureUnchanged
    summary = changes.summary()
    if len(summary) > MAX_SUMMARY_LENGTH:
        summary = summary[: MAX_SUMMARY_LENGTH - 1] + "…"
    return (
        NewRevision(
            architecture_id=parent.architecture_id,
            number=parent.number + 1,
            parent_number=parent.number,
            ir=ir,
            content_hash=content_hash(ir),
            source=source,
            summary=summary,
            reason=_reason(reason),
            created_by_user_id=created_by_user_id,
            requirement_set_id=requirement_set_id or parent.requirement_set_id,
        ),
        changes,
    )


def compare(before: ArchitectureRevision, after: ArchitectureRevision) -> ArchitectureDiff:
    """What changed between two revisions of the same architecture (in either direction)."""
    if before.architecture_id != after.architecture_id:
        raise InvalidRevision(
            "Only revisions of the same architecture can be compared.",
            details={"reason": "different_architectures"},
        )
    return diff(before.ir, after.ir)
