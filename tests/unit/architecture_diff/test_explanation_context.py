"""What the model is given (spec 13, 18): only the diff's own facts and passages, each citable by id;
secrets never; bounded, saying what was left out; the same inputs give the same context."""

from core.domain.architecture_diff.changes import FieldDelta
from core.domain.architecture_diff.ports import ExplanationBudget
from core.domain.architecture_diff.values import Basis, ChangeClass, ValueType
from engines.architecture_diff.explanation_context import (
    CONTEXT_REF,
    assemble,
    merged,
    retrieval_queries,
)
from tests.unit.architecture_agent.test_agent_context import passage
from tests.unit.architecture_diff.test_diff_domain import (
    a_diff,
    a_secret,
    added,
    modified,
    request,
    semantic,
)

BUDGET = ExplanationBudget()
SMALL = ExplanationBudget(max_context_chars=1000)


def test_everything_citable_is_listed_by_id() -> None:
    diff = a_diff()
    context = assemble(diff, (passage("kch_a", 1),), BUDGET).context
    assert context is not None
    assert context.citable[Basis.CHANGE] == {c.id for c in diff.semantic.changes}
    assert context.citable[Basis.GROUP] == {g.id for g in diff.semantic.groups}
    assert context.citable[Basis.EVIDENCE] == {"kch_a"}
    assert context.citable[Basis.FINDING] == context.citable[Basis.USER_INPUT] == frozenset()
    assert context.group_ids == tuple(g.id for g in diff.semantic.groups)
    for change in diff.semantic.changes:
        assert f"[{change.id}]" in context.text
    assert "configuration.replicas: 2 -> 4" in context.text


def test_the_persons_context_is_citable_only_when_given() -> None:
    diff = a_diff(request=request(context="We expect twice the orders at launch."))
    context = assemble(diff, (), BUDGET).context
    assert context is not None
    assert context.citable[Basis.USER_INPUT] == {CONTEXT_REF}
    assert dict(context.sections)["comparison_context"] == "We expect twice the orders at launch."


def test_a_secret_is_never_shown() -> None:
    change = modified(classes=(ChangeClass.SECURITY,), fields=(a_secret(),))
    context = assemble(a_diff(semantic=semantic(change)), (), BUDGET).context
    assert context is not None
    assert "api_key: (a secret value changed; its values are not shown)" in context.text
    assert "[redacted]" not in context.text


def test_field_details_give_way_first() -> None:
    fields = tuple(
        FieldDelta(
            f"configuration.extra.setting_{i:02}",
            "a-long-previous-value",
            "a-long-following-value",
            ValueType.TEXT,
            ValueType.TEXT,
            "configuration",
            (ChangeClass.CONFIGURATION,),
        )
        for i in range(20)
    )
    change = modified(classes=(ChangeClass.CONFIGURATION,), fields=fields)
    assembled = assemble(a_diff(semantic=semantic(change)), (), SMALL)
    assert assembled.context is not None
    assert f"[{change.id}]" in assembled.context.text
    assert "setting_00" not in assembled.context.text
    assert assembled.limitations == ("The changes' field details were left out to fit the context budget.",)


def test_passages_that_do_not_fit_are_said_to_be_left_out() -> None:
    long = (passage("kch_a", 1, "Orders " * 40), passage("kch_b", 2, "Orders " * 40))
    assembled = assemble(a_diff(), long, SMALL)
    assert assembled.context is not None
    assert [p.citation.chunk_id for p in assembled.evidence] == ["kch_a"]
    assert assembled.context.citable[Basis.EVIDENCE] == {"kch_a"}  # only what it was given
    assert assembled.limitations == ("1 retrieved passage(s) left out to fit the context budget.",)


def test_a_diff_too_large_to_explain_sends_nothing() -> None:
    changes = tuple(added(f"cache-{i:03}") for i in range(40))
    assembled = assemble(a_diff(semantic=semantic(*changes)), (), SMALL)
    assert assembled.context is None
    assert assembled.limitations


def test_the_context_is_deterministic() -> None:
    diff, passages = a_diff(), (passage("kch_a", 1), passage("kch_b", 2))
    assert assemble(diff, passages, BUDGET) == assemble(diff, passages, BUDGET)


def test_retrieval_is_bounded_and_skipped_when_nothing_changed() -> None:
    assert retrieval_queries(a_diff(semantic=semantic()), BUDGET) == ()
    assert retrieval_queries(a_diff(), ExplanationBudget(max_passages=0)) == ()
    [query] = retrieval_queries(a_diff(), BUDGET)
    assert query.text == "Orders API Cache"
    assert query.limit == BUDGET.max_passages


def test_merged_passages_are_each_kept_once_within_the_limit() -> None:
    first = (passage("kch_b", 2), passage("kch_a", 1))
    second = (passage("kch_a", 1), passage("kch_c", 1))
    found = merged((first, second), ExplanationBudget(max_passages=2))
    assert [p.citation.chunk_id for p in found] == ["kch_a", "kch_b"]
