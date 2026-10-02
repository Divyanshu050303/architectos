"""Retrieval and citations (Knowledge/RAG Engine, phase 5): one term rule for passages and queries;
exact identifiers first, then passages holding at least half the query's terms; another project's
passage never returned, whatever the index proposed; filters only narrow; every passage cited with
its source version and location; stale passages said to be stale; no padding — nothing found is
insufficient evidence, never evidence against; deterministic."""

import uuid
from datetime import UTC, datetime

from core.domain.decisions.entities import Decision, DecisionOption, DecisionStatus
from core.domain.knowledge.documents import KnowledgeChunk
from core.domain.knowledge.ingestion import IngestionRun
from core.domain.knowledge.ports import IngestionInput
from core.domain.knowledge.retrieval import (
    SEMANTIC_NOT_CONFIGURED,
    Candidate,
    RetrievalQuery,
    RetrievalResult,
    Scope,
)
from core.domain.knowledge.sources import KnowledgeSource, RecordRef
from core.domain.knowledge.uploads import DocumentUpload
from core.domain.knowledge.values import RetrievalMethod, SourceType, Trigger, Verification
from engines.knowledge.engine import DeterministicKnowledgeEngine
from engines.knowledge.terms import terms, tokens

AT = datetime(2026, 10, 2, tzinfo=UTC)
ENGINE = DeterministicKnowledgeEngine()
PROJECT, OTHER, USER = uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=9)
RUNBOOK = """\
# Orders runbook

Orders run on `orders-api` with PostgreSQL (ADR-3).

## Failover

Promote the read replica within 30 s. Replicas lag at most 2 s.

## Backups

Backups run nightly and are kept 30 days.
"""
NOTES = "Capacity notes.\n\nThe primary database handles 2,000 rps at peak.\n"


def passages(source: KnowledgeSource, content: IngestionInput) -> tuple[KnowledgeChunk, ...]:
    run = IngestionRun(uuid.uuid4(), source.project_id, source.id, Trigger.REGISTER, USER, AT)
    outcome = ENGINE.ingest(source, content, run, AT)
    assert outcome.indexed is not None
    return outcome.indexed.chunks


def upload(n: int, name: str, path: str, text: str, project: uuid.UUID = PROJECT) -> list[Candidate]:
    kind = SourceType.MARKDOWN if path.endswith(".md") else SourceType.TEXT
    source = KnowledgeSource(uuid.UUID(int=n), project, kind, name, USER, AT, AT, path=path)
    found = passages(source, IngestionInput(upload=DocumentUpload(path, text)))
    return [Candidate(project, c, name, kind, False, Verification.USER_PROVIDED) for c in found]


def adr(stale: bool = False) -> list[Candidate]:
    option = DecisionOption("evo_a", "Read replicas", "scaling", ("db.replicas = 3",), "valid", (), ())
    decision = Decision(
        uuid.UUID(int=30), PROJECT, uuid.UUID(int=31), 3, "Scale order reads", DecisionStatus.ACCEPTED,
        "Checkout reads saturate the primary database.", (option,), None, AT, chosen_option="evo_a",
        rationale="Replicas are cheaper than a cache.",
    )  # fmt: skip
    record = RecordRef(SourceType.DECISION, decision.id, "ADR-3")
    source = KnowledgeSource(
        uuid.UUID(int=40), PROJECT, SourceType.DECISION, "ADR-3", USER, AT, AT, record=record
    )
    found = passages(source, IngestionInput(decision=decision))
    return [
        Candidate(PROJECT, c, "ADR-3", SourceType.DECISION, stale, Verification.USER_PROVIDED, "accepted")
        for c in found
    ]


CANDIDATES = [
    *upload(10, "Runbook", "docs/runbook.md", RUNBOOK),
    *upload(11, "Capacity notes", "notes.txt", NOTES),
    *adr(),
]
FOREIGN = upload(20, "Their runbook", "docs/runbook.md", RUNBOOK + "\nReplica failover drill.\n", OTHER)
SCOPE = Scope(PROJECT, searched=3)


def ask(
    text: str | None = None, *, candidates: list[Candidate] | None = None, **query: object
) -> RetrievalResult:
    found = RetrievalQuery(text, **query)  # type: ignore[arg-type]
    return ENGINE.retrieve(found, CANDIDATES if candidates is None else candidates, SCOPE)


def test_one_term_rule_for_passages_and_queries() -> None:
    assert terms("The Read REPLICAS of the primary") == ("primary", "read", "replica")
    assert terms("Policies, boxes, classes, status, p95, 2,000 rps") == (
        "000", "2", "box", "class", "p95", "policy", "rps", "status",
    )  # fmt: skip
    assert tokens("replica Replicas replica") == ["replica", "replica", "replica"]
    assert terms("東京 リージョン") == ("リージョン", "東京")  # letters of any script are terms
    assert terms("the and of") == ()  # nothing but function words: nothing to match


def test_identifiers_are_found_exactly_and_first() -> None:
    result = ask("replica failover", identifiers=("ADR-3",))
    first = result.passages[0]
    assert (first.method, first.matched) == (RetrievalMethod.IDENTIFIER, ("ADR-3",))
    assert first.citation.source_name == "Runbook"  # the runbook names ADR-3 in its text
    methods = [p.method for p in result.passages]
    assert methods == sorted(methods, key=lambda m: m is RetrievalMethod.LEXICAL)  # identifiers, then terms
    missing = ask(identifiers=("ADR-99",))
    assert missing.insufficient_evidence
    assert "No indexed passage names ADR-99." in missing.limitations


def test_a_passage_must_hold_half_the_query_terms() -> None:
    result = ask("how fast is replica failover")  # terms: fast, replica, failover
    texts = [p.text for p in result.passages]
    assert any("Promote the read replica" in t for t in texts)
    best = result.passages[0]
    assert (best.method, set(best.matched)) == (RetrievalMethod.LEXICAL, {"replica", "failover"})
    assert best.limitations == ("Matches 2 of 3 query terms.",)
    common = ask("primary quantum entanglement teleportation")  # one of four terms: not evidence
    assert common.insufficient_evidence
    assert common.passages == ()


def test_another_projects_passage_is_never_returned() -> None:
    mixed = CANDIDATES + FOREIGN  # as if the index had proposed another project's passages
    result = ask("replica failover drill", candidates=mixed)
    assert result.passages
    assert all(p.citation.source_id != uuid.UUID(int=20) for p in result.passages)
    only_foreign = ask("replica failover drill", candidates=FOREIGN)
    assert only_foreign.insufficient_evidence


def test_filters_only_narrow() -> None:
    by_source = ask("primary database", source_ids=(uuid.UUID(int=11),))
    assert {p.citation.source_id for p in by_source.passages} == {uuid.UUID(int=11)}
    by_type = ask("primary database", source_types=(SourceType.DECISION,))
    assert {p.citation.source_type for p in by_type.passages} == {SourceType.DECISION}
    nowhere = ask("primary database", source_ids=(uuid.UUID(int=999),))
    assert nowhere.insufficient_evidence


def test_passages_are_cited_where_they_are_and_as_they_are_known() -> None:
    result = ask("backups nightly")
    found = result.passages[0]
    citation = found.citation
    assert (citation.source_name, citation.source_version, citation.locator.heading_path) == (
        "Runbook", 1, ("Orders runbook", "Backups"),
    )  # fmt: skip
    assert citation.reference == "Runbook (v1): Orders runbook > Backups (lines 9-11)"
    assert found.text == "## Backups\n\nBackups run nightly and are kept 30 days."
    assert found.verification is Verification.USER_PROVIDED  # retrieving it verifies nothing
    record = ask("replicas cheaper cache").passages[0]
    assert (record.citation.locator.reference(), record.record_status) == ("ADR-3 rationale", "accepted")
    shown = result.to_dict()
    assert not {"score", "similarity", "embedding", "confidence"} & set(shown["passages"][0])
    assert result.versions == {"knowledge-retrieval": 1, "knowledge-terms": 1}


def test_stale_passages_are_marked_and_can_be_excluded() -> None:
    stale = [c for c in CANDIDATES if c.source_type is not SourceType.DECISION] + adr(stale=True)
    found = ask("replicas cheaper cache", candidates=stale).passages[0]
    assert found.stale
    assert any("re-index the source" in x for x in found.limitations)
    excluded = ENGINE.retrieve(
        RetrievalQuery("replicas cheaper cache", include_stale=False),
        stale,
        Scope(PROJECT, 2, stale_excluded=1),
    )
    assert all(not p.stale for p in excluded.passages)
    assert "1 stale source(s) were excluded, as asked." in excluded.limitations


def test_results_are_never_padded_and_say_what_was_not_searched() -> None:
    limited = ask("replica", limit=1)
    assert len(limited.passages) == 1
    assert any(x.endswith("the first 1 are returned.") for x in limited.limitations)
    few = ask("nightly backups", limit=20)
    assert len(few.passages) == 1  # one passage supports it: one is returned
    nothing = ENGINE.retrieve(
        RetrievalQuery("kafka partitions"), CANDIDATES, Scope(PROJECT, 3, not_indexed=2)
    )
    assert (nothing.insufficient_evidence, nothing.searched_sources) == (True, 3)
    assert nothing.limitations == (
        SEMANTIC_NOT_CONFIGURED,
        "2 source(s) in scope are not indexed and were not searched.",
        "Nothing in the searched sources supports the query — which is not evidence that it is false.",
    )


def test_retrieval_is_deterministic_and_never_duplicates() -> None:
    query = RetrievalQuery("primary database replica", identifiers=("ADR-3",))
    first = ENGINE.retrieve(query, CANDIDATES, SCOPE)
    doubled = CANDIDATES * 2
    shuffled = list(reversed(doubled[5:] + doubled[:5]))  # the same passages, twice, in another order
    again = ENGINE.retrieve(query, shuffled, SCOPE)
    assert again == first
    assert len({p.citation.chunk_id for p in again.passages}) == len(again.passages)
