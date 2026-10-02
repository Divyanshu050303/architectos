"""Terms (rule ``knowledge-terms@1``): how passages and queries are reduced to the words they are matched
by — the same rule for both, so what is stored and what is asked are compared alike, whether the
comparison runs here or in the database.

A term is a run of letters and digits after Unicode compatibility case-folding (``Replica`` and
``REPLICA`` are one term; ``p95``, ``2000`` and non-Latin words are terms). Common English function
words (``the``, ``and``, ``of``…) are not terms. A plural is reduced by a fixed rule — ``-ies`` to
``-y``, ``-es`` after ``ss``/``x``/``z``/``ch``/``sh``, a final ``-s`` (not ``-ss``, ``-us``,
``-is``), for words of four letters or more (``rps`` stays ``rps``) — so ``replicas`` matches
``replica``; nothing else is stemmed, and no synonym or meaning is inferred.
"""

import re
import unicodedata

RULE = "knowledge-terms"
VERSION = 1
MAX_TERMS = 2000  # per passage
MAX_QUERY_TERMS = 32

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have", "how", "if",
    "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their", "there", "these",
    "this", "to", "was", "were", "what", "when", "where", "which", "who", "why", "will", "with",
})  # fmt: skip


def _singular(word: str) -> str:
    if len(word) <= 3 or not word.isalpha():
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    """Every term occurrence of ``text``, in order (for counting)."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return [_singular(w) for w in _WORD.findall(folded) if w not in STOPWORDS]


def terms(text: str, limit: int = MAX_TERMS) -> tuple[str, ...]:
    """The distinct terms of ``text``, sorted (at most ``limit``)."""
    return tuple(sorted(set(tokens(text))))[:limit]
