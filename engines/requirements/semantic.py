"""The engine's semantic extraction port: where a language model may help, and on what terms.

Deterministic first. The model is asked only when the rules leave requirement-like text unread
(``needs_semantic``), its proposals are merged under the rules' readings (a deterministic reading
of the same text always wins) and every proposal has already been validated by the implementation
(``ai/agents/requirement_agent.py``). A model failure never fails an analysis: the deterministic
result stands, with a finding saying semantic extraction was not available.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from core.domain.requirements.candidates import RequirementCandidate

from .extractor import Extraction, sentences


@dataclass(frozen=True, slots=True)
class Rejection:
    code: str  # llm_item_invalid, llm_quote_not_in_input, llm_proposal_invalid, llm_too_many
    detail: str
    quote: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticOutcome:
    """What semantic extraction produced; ``failure`` is set when the model gave nothing usable."""

    source: str  # provider/model
    prompt_version: str
    candidates: tuple[RequirementCandidate, ...] = ()  # validated drafts, source "ai"
    rejections: tuple[Rejection, ...] = ()
    failure: str | None = None  # llm_unavailable, llm_timeout, llm_malformed_output
    usage: dict[str, int] = field(default_factory=dict)


class SemanticExtractor(Protocol):
    @property
    def source(self) -> str: ...

    async def propose(self, raw_input: str) -> SemanticOutcome: ...


_REQUIREMENT_CUE = re.compile(
    r"\b(?:should|must|shall|needs?|required?|want|support|handle|allow|able\s+to|expect)\b", re.IGNORECASE
)


def needs_semantic(extraction: Extraction) -> str | None:
    """Why the rules need help, or None: unresolved quantities, or requirement-like sentences that
    produced no candidate."""
    if extraction.unresolved:
        return "unresolved_text"
    covered = [c.span for c in extraction.candidates if c.span is not None]
    for sentence in sentences(extraction.raw_input):
        touched = any(span.start < sentence.end and sentence.start < span.end for span in covered)
        if not touched and _REQUIREMENT_CUE.search(sentence.text):
            return "uncovered_sentences"
    return None


def merge(extraction: Extraction, outcome: SemanticOutcome) -> Extraction:
    """The model's candidates join the rules' ones, except where a rule already read the same text
    as the same kind of requirement."""
    kept = list(extraction.candidates)
    for candidate in outcome.candidates:
        span = candidate.span
        duplicate = span is not None and any(
            existing.span is not None
            and existing.span.start < span.end
            and span.start < existing.span.end
            and (existing.content.type, existing.content.category)
            == (candidate.content.type, candidate.content.category)
            for existing in extraction.candidates
        )
        if not duplicate:
            kept.append(candidate)
    return Extraction(extraction.raw_input, tuple(kept), extraction.unresolved, extraction.notes)
