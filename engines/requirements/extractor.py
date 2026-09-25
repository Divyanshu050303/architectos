"""Deterministic extraction: raw requirement text → requirement candidates, plus what could not be
read. Pure; the raw text is never modified and every candidate points at its exact span.

    for each sentence:
        normalize   → quantities as bounds, and the problems (ambiguous units, negative values, …)
        classify    → type / category / metric / scope for each bound, or why it cannot be
        qualitative → encryption, PII, GDPR, payments, regions, … (outside the quantities' text)

A candidate is a proposal (source ``system``, status draft); nothing here decides that it is right.
Every assumption made on the way (a year as 365 days, an operator the text did not state) is
returned as a note tied to the candidate, for the assumption engine to surface.
"""

import re
from dataclasses import dataclass, replace

from core.domain.requirements.candidates import ExtractionMethod, RequirementCandidate, SourceSpan
from core.domain.requirements.entities import RequirementContent
from core.domain.requirements.enums import RequirementStatus
from core.domain.requirements.errors import InvalidRequirement
from core.domain.requirements.value_objects import SetConstraint, parse_structured_data

from .classifier import Classification, Unclassified, classify, priority_of, qualitative, scope_in
from .normalizer import Bound, Interpretation, Percentile, normalize

# Sentence ends: . ! ? ; followed by whitespace, or line breaks. Decimal points ("99.9") are not
# followed by whitespace, so they never split a sentence.
_BOUNDARY = re.compile(r"(?<=[.!?;])\s+|\n+")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_SEPARATOR = re.compile(r"[,;]|\b(?:and|but|while|whereas|with)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Sentence:
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class Unresolved:
    """Something requirement-like the rules could not turn into a candidate, and why."""

    reason: str
    span: SourceSpan
    options: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Note:
    """An interpretation made while extracting a candidate."""

    candidate_key: str
    interpretation: Interpretation


@dataclass(frozen=True, slots=True)
class Extraction:
    raw_input: str
    candidates: tuple[RequirementCandidate, ...]
    unresolved: tuple[Unresolved, ...]
    notes: tuple[Note, ...]


def sentences(raw: str) -> list[Sentence]:
    found: list[Sentence] = []
    position = 0
    for boundary in [*_BOUNDARY.finditer(raw), None]:
        end = boundary.start() if boundary else len(raw)
        piece = raw[position:end]
        bullet = _BULLET.match(piece)
        start = position + (bullet.end() if bullet else 0)
        text = raw[start:end]
        stripped = text.strip()
        if stripped:
            leading = len(text) - len(text.lstrip())
            found.append(Sentence(start + leading, start + leading + len(stripped), stripped))
        position = boundary.end() if boundary else len(raw)
    return found


def operator_note(classification: Classification) -> Interpretation:
    return Interpretation(
        "operator_implied",
        f"No bound was stated; read as {classification.metric} {classification.operator.value} "
        "(the usual reading).",
    )


def _nearest_percentile(
    percentiles: tuple[Percentile, ...], lo: int, hi: int, anchor: int
) -> Percentile | None:
    inside = [p for p in percentiles if lo <= p.start and p.end <= hi]
    return min(inside, key=lambda p: abs(p.start - anchor), default=None)


def _clause(text: str, bound: Bound, bounds: tuple[Bound, ...], lo: int, hi: int) -> tuple[int, int]:
    """The sub-clause around ``bound``: from the separator before it to the one after it (commas,
    semicolons, and/but/while/with), never splitting another quantity ("2,000", "10 and 20 GB")."""
    inside = [(b.start, b.end) for b in bounds]
    separators = [
        m for m in _SEPARATOR.finditer(text, lo, hi) if not any(s <= m.start() < e for s, e in inside)
    ]
    before = [m.end() for m in separators if m.end() <= bound.start]
    after = [m.start() for m in separators if m.start() >= bound.end]
    return (max(before, default=lo), min(after, default=hi))


def _classify_in_context(
    text: str, bound: Bound, bounds: tuple[Bound, ...], lo: int, hi: int
) -> Classification | Unclassified:
    """Keywords in the quantity's own clause first ("the API should have p95 latency below 300ms");
    the wider context between neighbouring quantities only if the clause says nothing. Scope is only
    ever taken from the clause."""
    start, end = _clause(text, bound, bounds, lo, hi)
    narrow = classify(bound, text[start:end], bound.start - start)
    if not isinstance(narrow, Unclassified) or narrow.reason == "user_count_kind_unspecified":
        return narrow
    wide = classify(bound, text[lo:hi], bound.start - lo)
    if isinstance(wide, Classification):
        return replace(wide, scope=scope_in(text[start:end], bound.start - start))
    return narrow


def extract(raw: str) -> Extraction:
    candidates: list[RequirementCandidate] = []
    unresolved: list[Unresolved] = []
    notes: list[Note] = []
    seen_qualitative: set[tuple[str, str]] = set()
    for sentence in sentences(raw):
        normalized = normalize(sentence.text)
        offset = sentence.start
        for problem in normalized.problems:
            span = SourceSpan(offset + problem.start, offset + problem.end, problem.text)
            unresolved.append(Unresolved(problem.reason, span))
        bounds = normalized.bounds
        for index, bound in enumerate(bounds):
            lo = bounds[index - 1].end if index > 0 else 0
            hi = bounds[index + 1].start if index + 1 < len(bounds) else len(sentence.text)
            result = _classify_in_context(sentence.text, bound, bounds, lo, hi)
            span = SourceSpan(
                offset + bound.start, offset + bound.end, sentence.text[bound.start : bound.end]
            )
            if isinstance(result, Unclassified):
                unresolved.append(Unresolved(result.reason, span, result.options))
                continue
            try:
                candidate = _quantitative(sentence, bound, result, normalized.percentiles, lo, hi, span)
            except InvalidRequirement as error:  # e.g. "between 20 and 10 GB": an empty range
                unresolved.append(Unresolved(f"invalid_{error.details['reason']}", span))
                continue
            candidates.append(candidate)
            notes.extend(Note(candidate.key, i) for i in bound.interpretations)
            if result.operator_implied:
                notes.append(Note(candidate.key, operator_note(result)))
        candidates.extend(_qualitative(sentence, bounds, seen_qualitative))
    return Extraction(raw, tuple(candidates), tuple(unresolved), tuple(notes))


def _quantitative(
    sentence: Sentence,
    bound: Bound,
    result: Classification,
    percentiles: tuple[Percentile, ...],
    lo: int,
    hi: int,
    span: SourceSpan,
) -> RequirementCandidate:
    percentile = None
    if result.metric == "latency":
        nearest = _nearest_percentile(percentiles, lo, hi, bound.start)
        percentile = nearest.value if nearest else None
    constraint = parse_structured_data(bound.structured_data(result.metric, result.operator, percentile))
    title = f"{result.title} (p{percentile.normalize()})" if percentile is not None else result.title
    content = RequirementContent(
        type=result.type,
        category=result.category,
        title=title,
        statement=sentence.text,
        priority=priority_of(sentence.text),
        status=RequirementStatus.DRAFT,
        constraint=constraint,
        scope=result.scope,
    )
    return RequirementCandidate(ExtractionMethod.PATTERN, content, result.confidence, span)


def _qualitative(
    sentence: Sentence, bounds: tuple[Bound, ...], seen: set[tuple[str, str]]
) -> list[RequirementCandidate]:
    """Keyword requirements outside the quantities' own text ("500 orders/sec" is not an ordering
    feature); each category once per input."""
    masked = list(sentence.text)
    for bound in bounds:
        masked[bound.start : bound.end] = " " * (bound.end - bound.start)
    found: list[RequirementCandidate] = []
    for match in qualitative("".join(masked), sentence.start):
        identity = (match.type.value, match.category)
        if identity in seen:
            continue
        seen.add(identity)
        constraint = SetConstraint("regions", tuple(sorted(match.values))) if match.values else None
        content = RequirementContent(
            type=match.type,
            category=match.category,
            title=match.title,
            statement=sentence.text,
            priority=priority_of(sentence.text),
            status=RequirementStatus.DRAFT,
            constraint=constraint,
        )
        local_start, local_end = match.start - sentence.start, match.end - sentence.start
        span = SourceSpan(match.start, match.end, sentence.text[local_start:local_end])
        found.append(RequirementCandidate(ExtractionMethod.PATTERN, content, match.confidence, span))
    return found
