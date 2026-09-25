"""Requirement candidates: what extraction proposes, before a person confirms it.

    raw input ──extract──> candidates ──analyze──> findings ──person promotes──> canonical requirement

A candidate is *not* a requirement. It keeps where it came from (the exact span of the user's text,
never rewritten), how it was obtained (deterministic pattern or language model) and how sure the
interpretation is. Its content may even be invalid: invalid candidates are reported, never
promoted. Promotion builds a ``NewRequirement`` through the same validation as any other creation,
and always as a draft, since interpretations are not authoritative.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .entities import NewRequirement, RequirementContent
from .enums import RequirementSource, RequirementStatus
from .errors import InvalidRequirement
from .requirements import validate_content
from .value_objects import decimal_to_str, parse_confidence


class ExtractionMethod(StrEnum):
    PATTERN = "pattern"  # deterministic rules: reproducible, no model involved
    LLM = "llm"  # a language model's structured proposal, validated like everything else

    @property
    def source(self) -> RequirementSource:
        return RequirementSource.SYSTEM if self is ExtractionMethod.PATTERN else RequirementSource.AI


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Characters ``[start, end)`` of the raw input, and the text found there."""

    start: int
    end: int
    text: str

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start or len(self.text) != self.end - self.start:
            raise ValueError("a span covers at least one character and matches its text")

    def matches(self, raw_input: str) -> bool:
        return raw_input[self.start : self.end] == self.text


@dataclass(frozen=True, slots=True)
class RequirementCandidate:
    method: ExtractionMethod
    content: RequirementContent  # status is always draft; not validated at construction
    confidence: Decimal  # in the interpretation, 0-1 (see RequirementSource)
    span: SourceSpan | None = None  # None only when a model could not point at the text

    def __post_init__(self) -> None:
        if self.content.status is not RequirementStatus.DRAFT:
            raise ValueError("candidates are drafts")
        parse_confidence(self.confidence)  # raises InvalidRequirement outside [0, 1]

    @property
    def source(self) -> RequirementSource:
        return self.method.source

    @property
    def key(self) -> str:
        """Deterministic: the same interpretation of the same text always has the same key, so
        re-running an analysis reproduces it and promoting twice is recognisable."""
        content = self.content
        identity = {
            "method": self.method.value,
            "span": [self.span.start, self.span.end] if self.span else None,
            "type": content.type.value,
            "category": content.category,
            "scope": content.scope.value,
            "structured_data": content.structured_data,
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())
        return "cand_" + digest.hexdigest()[:16]

    def problem(self) -> InvalidRequirement | None:
        """Why this candidate could not be promoted as it is, if it could not."""
        content = self.content
        try:
            validate_content(content.type, content.category, content.status, content.constraint)
        except InvalidRequirement as error:
            return error
        return None

    def to_new_requirement(self, *, project_id: uuid.UUID, created_by_user_id: uuid.UUID) -> NewRequirement:
        """The draft requirement a promotion creates; validated like any creation."""
        content = self.content
        return NewRequirement.create(
            project_id=project_id,
            created_by_user_id=created_by_user_id,
            type=content.type,
            category=content.category,
            title=content.title,
            statement=content.statement,
            priority=content.priority,
            status=RequirementStatus.DRAFT,
            source=self.source,
            confidence=decimal_to_str(self.confidence),
            structured_data=content.structured_data,
            scope=content.scope,
        )
