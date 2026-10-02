"""Retrieval (rule ``knowledge-retrieval@1``): the passages that support a query, in a stated order, each
with its citation — decided here from candidates an index proposed, never trusting the index for scope.

1. **Scope.** A candidate of another project is dropped (a prefilter bug must never leak a passage);
   so is one outside the query's sources or source types, or a stale one when stale sources are
   excluded. Archived and never-indexed sources are not candidates at all.
2. **Exact identifiers.** A passage that names a requested identifier (``ADR-3``, ``REQ-12``, a
   backticked id) is an ``identifier`` match; more requested identifiers named rank first.
3. **Terms.** Otherwise a passage must contain at least half of the query's terms
   (``knowledge-terms@1``, at least one) to be a ``lexical`` match: more distinct terms, then more
   occurrences, then a shorter passage rank first. A match on a single common term of a long query
   is not evidence and is not returned.
4. **Order and limit.** Identifier matches, then lexical ones; ties by source, passage order and id —
   the same candidates always give the same result. The result holds at most ``limit`` passages and
   never more than matched: no passage is added to fill the count.

The result says what it could not do: semantic similarity is not configured; sources in scope that
are not indexed were not searched; stale sources excluded; identifiers no passage names; passages
beyond the limit. Nothing found is ``insufficient_evidence`` — never evidence that a statement is false.
"""

import math
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from core.domain.knowledge.retrieval import (
    SEMANTIC_NOT_CONFIGURED,
    Candidate,
    Citation,
    Passage,
    RetrievalQuery,
    RetrievalResult,
    Scope,
)
from core.domain.knowledge.values import RetrievalMethod

from . import terms as term_rule

RULE = "knowledge-retrieval"
VERSION = 1
MIN_COVERAGE = 0.5
STALE = "The record has changed since this passage was indexed: re-index the source to read it as it is now."


@dataclass(frozen=True, slots=True)
class _Match:
    key: tuple[object, ...]
    candidate: Candidate
    method: RetrievalMethod
    matched: tuple[str, ...]
    partial: str | None = None


def _in_scope(candidate: Candidate, query: RetrievalQuery, project_id: uuid.UUID) -> bool:
    chunk = candidate.chunk
    return (
        candidate.project_id == project_id
        and (not query.source_ids or chunk.source_id in query.source_ids)
        and (not query.source_types or candidate.source_type in query.source_types)
        and (query.include_stale or not candidate.stale)
    )


def _match(candidate: Candidate, query: RetrievalQuery, asked: tuple[str, ...]) -> _Match | None:
    chunk = candidate.chunk
    named = tuple(sorted(set(query.identifiers) & set(chunk.identifiers)))
    place = (candidate.source_name, str(chunk.source_id), chunk.sequence, chunk.id)
    if named:
        return _Match((0, -len(named), *place), candidate, RetrievalMethod.IDENTIFIER, named)
    if not asked:
        return None
    needed = max(1, math.ceil(len(asked) * MIN_COVERAGE))
    words = Counter(term_rule.tokens(chunk.text))
    matched = tuple(t for t in asked if t in words)
    if len(matched) < needed:
        return None
    hits = sum(words[t] for t in matched)
    partial = None if len(matched) == len(asked) else f"Matches {len(matched)} of {len(asked)} query terms."
    key = (1, -len(matched), -hits, len(chunk.text), *place)
    return _Match(key, candidate, RetrievalMethod.LEXICAL, matched, partial)


def _passage(found: _Match, rank: int) -> Passage:
    candidate, chunk = found.candidate, found.candidate.chunk
    citation = Citation(
        chunk.source_id, candidate.source_name, candidate.source_type, chunk.source_version,
        chunk.document_id, chunk.id, chunk.locator,
    )  # fmt: skip
    limitations = tuple(x for x in (found.partial, STALE if candidate.stale else None) if x)
    return Passage(
        citation, chunk.text, found.method, rank, candidate.verification, candidate.record_status,
        candidate.stale, found.matched, limitations,
    )  # fmt: skip


def retrieve(query: RetrievalQuery, candidates: Iterable[Candidate], scope: Scope) -> RetrievalResult:
    asked = term_rule.terms(query.text, term_rule.MAX_QUERY_TERMS) if query.text else ()
    best: dict[str, _Match] = {}
    for candidate in candidates:
        if not _in_scope(candidate, query, scope.project_id):
            continue
        found = _match(candidate, query, asked)
        if found is None:
            continue
        kept = best.get(candidate.chunk.id)
        if kept is None or found.key < kept.key:
            best[candidate.chunk.id] = found
    ordered = sorted(best.values(), key=lambda m: m.key)
    shown = ordered[: query.limit]
    limitations = [SEMANTIC_NOT_CONFIGURED]
    named = {i for m in ordered if m.method is RetrievalMethod.IDENTIFIER for i in m.matched}
    missing = [i for i in query.identifiers if i not in named]
    if missing:
        limitations.append(f"No indexed passage names {', '.join(missing)}.")
    if scope.not_indexed:
        limitations.append(f"{scope.not_indexed} source(s) in scope are not indexed and were not searched.")
    if scope.stale_excluded:
        limitations.append(f"{scope.stale_excluded} stale source(s) were excluded, as asked.")
    if len(ordered) > len(shown):
        limitations.append(f"{len(ordered)} passages matched; the first {len(shown)} are returned.")
    if not shown:
        limitations.append(
            "Nothing in the searched sources supports the query — which is not evidence that it is false."
        )
    return RetrievalResult(
        tuple(_passage(m, rank) for rank, m in enumerate(shown, start=1)),
        scope.searched,
        tuple(limitations),
        {RULE: VERSION, term_rule.RULE: term_rule.VERSION},
    )
