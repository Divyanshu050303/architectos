"""Immutable, numbered revisions (Architecture IR phase 3)."""

import dataclasses
import uuid
from datetime import UTC, datetime

import pytest

from core.architecture_ir.commands import ChangeReplicas, RenameNode, apply_commands
from core.architecture_ir.serialization import content_hash
from core.domain.architecture.errors import ArchitectureUnchanged, InvalidRevision
from core.domain.architecture.versions import (
    ArchitectureRevision,
    NewRevision,
    RevisionSource,
    compare,
    first_revision,
    next_revision,
    restored_revision,
)
from tests.unit.architecture_ir.builders import api_and_postgres

ARCHITECTURE, ADA, REQUIREMENT_SET = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
NOW = datetime(2026, 9, 26, tzinfo=UTC)


def stored(new: NewRevision) -> ArchitectureRevision:
    return ArchitectureRevision(
        id=uuid.uuid4(), created_at=NOW, **{f.name: getattr(new, f.name) for f in dataclasses.fields(new)}
    )


def test_the_first_revision() -> None:
    ir = api_and_postgres()
    new = first_revision(
        ARCHITECTURE,
        ir,
        source=RevisionSource.USER,
        created_by_user_id=ADA,
        requirement_set_id=REQUIREMENT_SET,
    )
    assert (new.number, new.parent_number, new.ir_schema_version) == (1, None, 1)
    assert new.content_hash == content_hash(ir)
    assert new.summary == "Created with 3 nodes and 2 connections."
    assert stored(new).label == "v1"


def test_each_revision_follows_its_parent_and_says_what_changed() -> None:
    first = stored(
        first_revision(
            ARCHITECTURE,
            api_and_postgres(),
            source=RevisionSource.USER,
            created_by_user_id=ADA,
            requirement_set_id=REQUIREMENT_SET,
        )
    )
    edited = apply_commands(first.ir, [ChangeReplicas("api", 6), RenameNode("db", "Primary DB")])
    new, changes = next_revision(
        first,
        edited,
        source=RevisionSource.AI,
        created_by_user_id=ADA,
        reason="  Scale out for Black Friday. ",
    )
    assert (new.number, new.parent_number, new.source) == (2, 1, RevisionSource.AI)
    assert new.summary == changes.summary() == "2 nodes modified."
    assert new.reason == "Scale out for Black Friday."
    assert new.requirement_set_id == REQUIREMENT_SET  # carried over
    assert new.content_hash != first.content_hash
    second = stored(new)
    assert compare(first, second).to_dict() == changes.to_dict()
    assert first.ir == api_and_postgres()  # the parent is untouched


def test_an_edit_that_changes_nothing_creates_no_revision() -> None:
    first = stored(
        first_revision(ARCHITECTURE, api_and_postgres(), source=RevisionSource.USER, created_by_user_id=ADA)
    )
    with pytest.raises(ArchitectureUnchanged):
        next_revision(first, api_and_postgres(), source=RevisionSource.USER, created_by_user_id=ADA)


def test_revisions_are_immutable() -> None:
    revision = stored(
        first_revision(ARCHITECTURE, api_and_postgres(), source=RevisionSource.USER, created_by_user_id=ADA)
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        revision.number = 7  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        revision.ir.nodes = ()  # type: ignore[misc]


def test_reasons_and_comparisons_are_checked() -> None:
    with pytest.raises(InvalidRevision):
        first_revision(
            ARCHITECTURE,
            api_and_postgres(),
            source=RevisionSource.USER,
            created_by_user_id=ADA,
            reason="x" * 501,
        )
    one = stored(
        first_revision(ARCHITECTURE, api_and_postgres(), source=RevisionSource.USER, created_by_user_id=ADA)
    )
    other = stored(
        first_revision(uuid.uuid4(), api_and_postgres(), source=RevisionSource.USER, created_by_user_id=ADA)
    )
    with pytest.raises(InvalidRevision):
        compare(one, other)


def test_restoring_is_a_new_revision_with_the_old_content() -> None:
    first = stored(
        first_revision(ARCHITECTURE, api_and_postgres(), source=RevisionSource.USER, created_by_user_id=ADA)
    )
    second_new, _ = next_revision(
        first,
        apply_commands(first.ir, [ChangeReplicas("api", 7)]),
        source=RevisionSource.USER,
        created_by_user_id=ADA,
    )
    second = stored(second_new)
    restored, changes = restored_revision(second, first, created_by_user_id=ADA, reason="Undo")
    assert (restored.number, restored.parent_number, restored.restored_from) == (3, 2, 1)
    assert (restored.ir, restored.content_hash) == (first.ir, first.content_hash)
    assert restored.summary == f"Restored v1: {changes.summary()}"
    with pytest.raises(ArchitectureUnchanged):  # restoring the current content creates nothing
        restored_revision(second, second, created_by_user_id=ADA)
    elsewhere = stored(
        first_revision(uuid.uuid4(), api_and_postgres(), source=RevisionSource.USER, created_by_user_id=ADA)
    )
    with pytest.raises(InvalidRevision):
        restored_revision(second, elsewhere, created_by_user_id=ADA)
