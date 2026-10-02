"""Knowledge retrieval, measured on the labelled v1 set: it must never regress, must be deterministic, and
the set itself must stay well-formed (every label points at something that exists)."""

import json

from ai.evaluation.knowledge import (
    CEILINGS,
    DATASET,
    check,
    corpus_files,
    evaluate,
    load_corpus,
    load_queries,
    thresholds,
)
from engines.knowledge.structure import markdown


def test_quality_does_not_regress() -> None:
    evaluation = evaluate()
    assert check(evaluation.metrics()) == [], evaluation.misses()


def test_the_evaluation_is_deterministic() -> None:
    assert evaluate().metrics() == evaluate().metrics()


def test_every_metric_has_a_threshold_and_ceilings_hold_at_zero() -> None:
    measured = set(evaluate().metrics()) - {"queries"}
    assert set(thresholds()) == measured
    assert {name: thresholds()[name] for name in CEILINGS} == {"false_evidence": 0, "duplicates": 0}


def test_the_set_is_well_formed_and_its_size_is_stated() -> None:
    queries = load_queries()
    corpus = load_corpus()
    names = {p.name for p in corpus_files()}
    assert len({q["id"] for q in queries}) == len(queries) == 24
    answerable = [q for q in queries if q["expected"]]
    assert len(answerable) == 20
    assert sum("identifiers" in q["query"] for q in answerable) == 3
    assert sum("sources" in q["query"] for q in queries) == 3
    sections = {
        path.name: {s.heading_path[-1] for s in markdown(path.read_text(encoding="utf-8")) if s.heading_path}
        for path in corpus_files()
    }
    for query in queries:
        assert set(query["query"].get("sources", ())) <= names, query["id"]
        for expected in query["expected"]:
            assert expected["source"] in names, query["id"]
            if "section" in expected:
                assert expected["section"] in sections[expected["source"]], query["id"]
            if "contains" in expected:
                assert expected["contains"] in corpus.texts[expected["source"]], query["id"]
    readme = (DATASET / "README.md").read_text(encoding="utf-8")
    assert "24 queries" in readme
    assert "## Known misses" in readme
    assert json.loads((DATASET / "thresholds.json").read_text(encoding="utf-8")) == thresholds()
