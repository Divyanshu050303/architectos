"""Measures knowledge retrieval on a small, versioned, labelled set of architecture documents and questions.

    python -m ai.evaluation.knowledge                 # the report, as text
    python -m ai.evaluation.knowledge --json          # the metrics, as JSON
    python -m ai.evaluation.knowledge --check         # exit 1 below the recorded thresholds

The corpus (``datasets/knowledge/v1/corpus``) is indexed with the real engine — adapters, redaction,
structure, chunking, terms — and every query (``queries.jsonl``) is answered by the real retrieval
rules over every passage (what the database prefilter proposes is a superset of what the rules
accept; an integration test checks the API returns the same passages). Measured:

- ``top1_accuracy``: answerable queries whose first passage is an expected one;
- ``recall_at_k``: expected passages found among the passages returned (``limit``, default 10);
- ``citation_accuracy``: returned passages whose citation locates their text exactly in the source;
- ``scope_accuracy``: filtered queries returning nothing outside their filter;
- ``empty_accuracy``: unanswerable queries answered with insufficient evidence;
- ``false_evidence`` (a ceiling): passages returned for unanswerable queries;
- ``duplicates`` (a ceiling): passages returned twice in one result.

**What this does not show.** Six documents and two dozen questions measure that the rules behave as
documented on representative text — not retrieval quality on a real project's documentation, nor
anything about semantic similarity (not configured). Deterministic: the same code always scores the
same.
"""

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.domain.knowledge.ingestion import IngestionRun
from core.domain.knowledge.ports import IngestionInput
from core.domain.knowledge.retrieval import Candidate, Passage, RetrievalQuery, RetrievalResult, Scope
from core.domain.knowledge.sources import KnowledgeSource
from core.domain.knowledge.uploads import DocumentUpload, type_of
from core.domain.knowledge.values import Trigger, Verification
from engines.knowledge.engine import DeterministicKnowledgeEngine

DATASET = Path(__file__).parent / "datasets" / "knowledge" / "v1"
THRESHOLDS = DATASET / "thresholds.json"
CEILINGS = {"false_evidence", "duplicates"}
PROJECT = uuid.UUID(int=1)
USER = uuid.UUID(int=2)
AT = datetime(2026, 10, 2, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Corpus:
    sources: dict[str, uuid.UUID]  # by path
    texts: dict[str, str]  # by path, as read
    candidates: tuple[Candidate, ...]


def corpus_files(dataset: Path = DATASET) -> list[Path]:
    return [p for p in sorted((dataset / "corpus").iterdir()) if type_of(p.name) is not None]


def load_corpus(dataset: Path = DATASET, engine: DeterministicKnowledgeEngine | None = None) -> Corpus:
    engine = engine or DeterministicKnowledgeEngine()
    sources: dict[str, uuid.UUID] = {}
    texts: dict[str, str] = {}
    candidates: list[Candidate] = []
    for number, path in enumerate(corpus_files(dataset), start=1):
        content = path.read_text(encoding="utf-8")
        upload = DocumentUpload(path.name, content)
        kind = upload.source_type
        source = KnowledgeSource(
            uuid.UUID(int=100 + number), PROJECT, kind, path.name, USER, AT, AT, path=path.name
        )
        run = IngestionRun(uuid.UUID(int=1000 + number), PROJECT, source.id, Trigger.REGISTER, USER, AT)
        outcome = engine.ingest(source, IngestionInput(upload=upload), run, AT)
        if outcome.indexed is None:
            raise ValueError(f"{path.name} could not be indexed: {[e.code for e in outcome.run.errors]}")
        sources[path.name] = source.id
        texts[path.name] = content
        candidates.extend(
            Candidate(PROJECT, chunk, path.name, kind, False, Verification.USER_PROVIDED)
            for chunk in outcome.indexed.chunks
        )
    return Corpus(sources, texts, tuple(candidates))


def load_queries(dataset: Path = DATASET) -> list[dict[str, Any]]:
    with (dataset / "queries.jsonl").open(encoding="utf-8") as lines:
        return [json.loads(line) for line in lines if line.strip()]


def query_of(spec: dict[str, Any], sources: dict[str, uuid.UUID]) -> RetrievalQuery:
    return RetrievalQuery(
        spec.get("text"),
        tuple(spec.get("identifiers", ())),
        tuple(sources[name] for name in spec.get("sources", ())),
        limit=spec.get("limit", 10),
    )


def matches(passage: Passage, expected: dict[str, Any]) -> bool:
    citation = passage.citation
    if citation.source_name != expected["source"]:
        return False
    if "section" in expected and citation.locator.heading_path[-1:] != (expected["section"],):
        return False
    return "contains" not in expected or expected["contains"] in passage.text


def cited_exactly(passage: Passage, texts: dict[str, str]) -> bool:
    locator = passage.citation.locator
    if locator.line_start is None or locator.line_end is None:
        return False
    lines = texts[passage.citation.source_name].replace("\r\n", "\n").split("\n")
    return passage.text in "\n".join(lines[locator.line_start - 1 : locator.line_end])


@dataclass(frozen=True, slots=True)
class Outcome:
    id: str
    expected: list[dict[str, Any]]
    filtered: bool
    allowed: frozenset[uuid.UUID]
    result: RetrievalResult


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 1.0


@dataclass(frozen=True, slots=True)
class Evaluation:
    outcomes: tuple[Outcome, ...]
    texts: dict[str, str]

    def metrics(self) -> dict[str, float]:
        answerable = [o for o in self.outcomes if o.expected]
        unanswerable = [o for o in self.outcomes if not o.expected]
        filtered = [o for o in self.outcomes if o.filtered]
        passages = [p for o in self.outcomes for p in o.result.passages]
        expected = [(o, e) for o in answerable for e in o.expected]
        found = sum(any(matches(p, e) for p in o.result.passages) for o, e in expected)
        top1 = sum(
            bool(o.result.passages) and any(matches(o.result.passages[0], e) for e in o.expected)
            for o in answerable
        )
        in_scope = sum(all(p.citation.source_id in o.allowed for p in o.result.passages) for o in filtered)
        repeated = sum(
            len(o.result.passages) - len({p.citation.chunk_id for p in o.result.passages})
            for o in self.outcomes
        )
        return {
            "queries": float(len(self.outcomes)),
            "top1_accuracy": _ratio(top1, len(answerable)),
            "recall_at_k": _ratio(found, len(expected)),
            "citation_accuracy": _ratio(sum(cited_exactly(p, self.texts) for p in passages), len(passages)),
            "scope_accuracy": _ratio(in_scope, len(filtered)),
            "empty_accuracy": _ratio(
                sum(o.result.insufficient_evidence for o in unanswerable), len(unanswerable)
            ),
            "false_evidence": float(sum(len(o.result.passages) for o in unanswerable)),
            "duplicates": float(repeated),
        }

    def misses(self) -> list[str]:
        """Each expected passage not found, and each passage returned for an unanswerable query."""
        found: list[str] = []
        for o in self.outcomes:
            for e in o.expected:
                if not any(matches(p, e) for p in o.result.passages):
                    found.append(f"{o.id}: missing {e}")
            if not o.expected and o.result.passages:
                found.append(f"{o.id}: returned {[p.citation.reference for p in o.result.passages]}")
        return found


def evaluate(dataset: Path = DATASET, engine: DeterministicKnowledgeEngine | None = None) -> Evaluation:
    engine = engine or DeterministicKnowledgeEngine()
    corpus = load_corpus(dataset, engine)
    outcomes = []
    for spec in load_queries(dataset):
        query = query_of(spec["query"], corpus.sources)
        allowed = frozenset(query.source_ids) or frozenset(corpus.sources.values())
        result = engine.retrieve(query, corpus.candidates, Scope(PROJECT, len(allowed)))
        outcomes.append(Outcome(spec["id"], spec["expected"], bool(query.source_ids), allowed, result))
    return Evaluation(tuple(outcomes), corpus.texts)


def thresholds(path: Path = THRESHOLDS) -> dict[str, float]:
    loaded: dict[str, float] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def check(metrics: dict[str, float], limits: dict[str, float] | None = None) -> list[str]:
    """Each metric at or above its threshold (ceilings: at or below). Thresholds only move up."""
    failures = []
    for name, limit in (limits if limits is not None else thresholds()).items():
        value = metrics.get(name)
        if value is None:
            failures.append(f"{name}: not measured")
        elif name in CEILINGS and value > limit:
            failures.append(f"{name}: {value:g} is above the ceiling {limit:g}")
        elif name not in CEILINGS and value < limit:
            failures.append(f"{name}: {value:g} is below the threshold {limit:g}")
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true", help="print the metrics as JSON")
    parser.add_argument("--check", action="store_true", help="exit 1 if a metric is below its threshold")
    args = parser.parse_args(argv)
    evaluation = evaluate()
    metrics = evaluation.metrics()
    if args.json:
        print(json.dumps(metrics, indent=2, sort_keys=True))  # noqa: T201 - a command-line report
    else:
        for name, value in sorted(metrics.items()):
            print(f"{name:20} {value:g}")  # noqa: T201
        for miss in evaluation.misses():
            print(f"- {miss}")  # noqa: T201
    failures = check(metrics)
    for failure in failures:
        print(failure, file=sys.stderr)  # noqa: T201
    return 1 if args.check and failures else 0


if __name__ == "__main__":
    sys.exit(main())
