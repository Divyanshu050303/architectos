"""The Requirement aggregate. Immutable: every change goes through ``revise``, which returns a
``Revision`` (the next state plus why it changed) that is persisted as a new, immutable version.
A requirement's history is never rewritten; see docs/domain/requirements.md.

Fixed at creation: project, number, type, source, confidence and creator. A requirement of a
different type is a different requirement: create a new one and deprecate the old.
"""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from core.domain.errors import NothingToUpdate

from .analyses import Origin
from .enums import (
    RequirementPriority,
    RequirementScope,
    RequirementSource,
    RequirementStatus,
    RequirementType,
)
from .errors import ChangeReasonRequired, RequirementLocked, RequirementNotFound, RequirementVersionConflict
from .normalization import normalize_structured_data
from .requirements import (
    CONFIDENCE_REQUIRED,
    IN_FORCE,
    INITIAL_STATUSES,
    check_transition,
    content_locked,
    validate_content,
)
from .value_objects import (
    KEEP,
    Keep,
    StructuredConstraint,
    invalid,
    normalize_change_reason,
    normalize_identifier,
    normalize_statement,
    normalize_title,
    parse_confidence,
    parse_structured_data,
)


@dataclass(frozen=True, slots=True)
class RequirementContent:
    """One state of a requirement: what a version records. Constructing one does not validate it:
    stored history must stay loadable even after the rules tighten. New states are validated
    explicitly (``validated``) by ``NewRequirement.create`` and ``Requirement.revise``."""

    type: RequirementType
    category: str
    title: str
    statement: str
    priority: RequirementPriority
    status: RequirementStatus
    constraint: StructuredConstraint | None
    # What it applies to; "system" when the statement does not say (never invented).
    scope: RequirementScope = RequirementScope.SYSTEM

    def validated(self) -> RequirementContent:
        validate_content(self.type, self.category, self.status, self.constraint)
        return self

    @property
    def structured_data(self) -> dict[str, Any]:
        return self.constraint.to_dict() if self.constraint is not None else {}

    @property
    def in_force(self) -> bool:
        return self.status in IN_FORCE


@dataclass(frozen=True, slots=True)
class NewRequirement:
    """A validated creation request, before it has an id, a number or timestamps."""

    project_id: uuid.UUID
    content: RequirementContent
    source: RequirementSource
    confidence: Decimal | None
    created_by_user_id: uuid.UUID
    origin: Origin | None = None  # set when promoted from an analysis's candidate

    @classmethod
    def create(  # noqa: PLR0913 - keyword-only, one argument per field of the request
        cls,
        *,
        project_id: uuid.UUID,
        created_by_user_id: uuid.UUID,
        type: RequirementType,
        category: str,
        title: str,
        statement: str,
        priority: RequirementPriority,
        status: RequirementStatus = RequirementStatus.DRAFT,
        source: RequirementSource = RequirementSource.USER,
        confidence: object = None,
        structured_data: object = None,
        scope: RequirementScope = RequirementScope.SYSTEM,
        origin: Origin | None = None,
    ) -> NewRequirement:
        if status not in INITIAL_STATUSES[source]:
            raise invalid("status", "not_allowed_at_creation")
        if source in CONFIDENCE_REQUIRED and confidence is None:
            raise invalid("confidence", f"required_for_{source.value}")
        if source is RequirementSource.USER and confidence is not None:
            raise invalid("confidence", "not_for_user")
        return cls(
            project_id=project_id,
            content=RequirementContent(
                type=type,
                category=normalize_identifier(category, "category"),
                title=normalize_title(title),
                statement=normalize_statement(statement),
                priority=priority,
                status=status,
                constraint=parse_structured_data(
                    normalize_structured_data(structured_data if structured_data is not None else {})
                ),
                scope=scope,
            ).validated(),
            source=source,
            confidence=parse_confidence(confidence) if confidence is not None else None,
            created_by_user_id=created_by_user_id,
            origin=origin,
        )


@dataclass(frozen=True, slots=True)
class RequirementChanges:
    """What a revision asks to change; None (or KEEP for structured data) leaves a field as is.
    ``structured_data={}`` removes the constraint."""

    category: str | None = None
    title: str | None = None
    statement: str | None = None
    priority: RequirementPriority | None = None
    status: RequirementStatus | None = None
    structured_data: dict[str, Any] | Keep = KEEP
    scope: RequirementScope | None = None

    @property
    def is_empty(self) -> bool:
        return self == RequirementChanges()


@dataclass(frozen=True, slots=True)
class Requirement:
    id: uuid.UUID
    project_id: uuid.UUID
    number: int
    version: int
    content: RequirementContent
    source: RequirementSource
    confidence: Decimal | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    origin: Origin | None = None  # the analysis and candidate it was promoted from, if any

    @property
    def reference(self) -> str:
        return f"REQ-{self.number}"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def revise(
        self,
        *,
        expected_version: int,
        changes: RequirementChanges,
        change_reason: str | None,
        author_user_id: uuid.UUID,
    ) -> Revision | None:
        """The next state as a new version, or None when the changes leave everything as it is.

        Checked in order: not deleted, not stale (optimistic concurrency), the status transition,
        the content lock, the change reason for requirements in force, then full validation."""
        if self.is_deleted:
            raise RequirementNotFound
        if changes.is_empty:
            raise NothingToUpdate
        if expected_version != self.version:
            raise RequirementVersionConflict(details={"current_version": self.version})

        current = self.content
        target = replace(
            current,
            category=normalize_identifier(changes.category, "category")
            if changes.category is not None
            else current.category,
            title=normalize_title(changes.title) if changes.title is not None else current.title,
            statement=normalize_statement(changes.statement)
            if changes.statement is not None
            else current.statement,
            priority=changes.priority or current.priority,
            status=changes.status or current.status,
            scope=changes.scope or current.scope,
            constraint=parse_structured_data(normalize_structured_data(changes.structured_data))
            if not isinstance(changes.structured_data, Keep)
            else current.constraint,
        )
        if target == current:
            return None

        check_transition(current.status, target.status)
        if replace(target, status=current.status) != current and content_locked(
            current.status, target.status
        ):
            raise RequirementLocked(details={"status": current.status.value})
        reason = normalize_change_reason(change_reason)
        if current.in_force and reason is None:
            raise ChangeReasonRequired
        return Revision(
            requirement=replace(self, version=self.version + 1, content=target.validated()),
            change_reason=reason,
            author_user_id=author_user_id,
        )

    def delete(self, at: datetime) -> Requirement:
        """Soft delete: the requirement disappears from the project; its versions stay, so anything
        that referenced them (requirement sets, architectures) remains explainable."""
        if self.is_deleted:
            raise RequirementNotFound
        return replace(self, deleted_at=at)


@dataclass(frozen=True, slots=True)
class Revision:
    """A requirement's next state and why it changed; persisted as version ``requirement.version``."""

    requirement: Requirement
    change_reason: str | None
    author_user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class RequirementVersion:
    """One immutable state of a requirement."""

    requirement_id: uuid.UUID
    version: int
    content: RequirementContent
    source: RequirementSource
    confidence: Decimal | None
    change_reason: str | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
