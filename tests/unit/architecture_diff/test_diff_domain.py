import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from core.architecture_ir.diff import ChangeKind
from core.architecture_ir.errors import ElementType
from core.domain.architecture_agent.requests import AgentUsage
from core.domain.architecture_diff.changes import Change, ChangeGroup, FieldDelta, SemanticDiff, change_id
from core.domain.architecture_diff.diffs import ArchitectureDiff
from core.domain.architecture_diff.errors import InvalidDiffRecord, InvalidDiffRequest
from core.domain.architecture_diff.explanations import (
    DiffExplanation,
    ExplanationRun,
    Grounding,
    GroupExplanation,
    Statement,
)
from core.domain.architecture_diff.impacts import (
    DecisionImpact,
    EngineImpact,
    FindingDelta,
    MeasureDelta,
    RequirementImpact,
)
from core.domain.architecture_diff.references import ComparedState, DiffRequest, StateRef
from core.domain.architecture_diff.values import (
    Basis,
    ChangeClass,
    ExplanationFailure,
    ExplanationStatus,
    FindingState,
    ImpactStatus,
    RequirementRelation,
    Sensitivity,
    StateKind,
    ValueType,
)

NOW = datetime(2026, 10, 3, tzinfo=UTC)
USER = uuid.uuid4()
ARCH = uuid.uuid4()
BASE_HASH, TARGET_HASH = "a" * 64, "b" * 64


def replicas(before: int = 2, after: int = 4) -> FieldDelta:
    return FieldDelta(
        "configuration.replicas",
        before,
        after,
        ValueType.NUMBER,
        ValueType.NUMBER,
        "resources",
        (ChangeClass.SCALING,),
    )


def modified(element_id: str = "api", **overrides: Any) -> Change:
    fields: dict[str, Any] = {
        "id": change_id(ElementType.NODE, element_id),
        "element": ElementType.NODE,
        "element_id": element_id,
        "change": ChangeKind.MODIFIED,
        "label": "Orders API",
        "kind": "service",
        "classes": (ChangeClass.SCALING,),
        "fields": (replicas(),),
    }
    return Change(**(fields | overrides))


def added(element_id: str = "cache") -> Change:
    return Change(
        change_id(ElementType.NODE, element_id),
        ElementType.NODE,
        element_id,
        ChangeKind.ADDED,
        "Cache",
        "cache",
        (ChangeClass.STRUCTURAL,),
    )


def semantic(*changes: Change) -> SemanticDiff:
    groups = tuple(
        ChangeGroup.of("shared_element", c.label or c.element_id, "Same element.", (c,)) for c in changes
    )
    return SemanticDiff(BASE_HASH, TARGET_HASH, changes, groups)


def request(**overrides: Any) -> DiffRequest:
    fields: dict[str, Any] = {"base": StateRef.revision(ARCH, 1), "target": StateRef.revision(ARCH, 2)}
    return DiffRequest(**(fields | overrides))


def a_diff(**overrides: Any) -> ArchitectureDiff:
    asked = request()
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "request": asked,
        "base": ComparedState(asked.base, BASE_HASH, "Orders v1"),
        "target": ComparedState(asked.target, TARGET_HASH, "Orders v2"),
        "semantic": semantic(modified(), added()),
        "requested_by_user_id": USER,
        "created_at": NOW,
    }
    return ArchitectureDiff(**(fields | overrides))


def a_secret(before: Any = "[redacted]", kind: ValueType = ValueType.REDACTED) -> FieldDelta:
    return FieldDelta(
        "configuration.extra.api_key",
        before,
        before,
        kind,
        kind,
        "configuration",
        (ChangeClass.SECURITY,),
        Sensitivity.SECRET,
    )


# --- references and requests -------------------------------------------------------------------


def test_states_are_named_exactly() -> None:
    assert StateRef.revision(ARCH, 3).to_dict()["revision_number"] == 3
    run = uuid.uuid4()
    assert StateRef.candidate(run).kind is StateKind.CANDIDATE
    with pytest.raises(InvalidDiffRequest):
        StateRef(StateKind.REVISION, architecture_id=ARCH)  # no number: never "the latest"
    with pytest.raises(InvalidDiffRequest):
        StateRef(StateKind.CANDIDATE, run_id=run, architecture_id=ARCH)  # one kind of reference
    with pytest.raises(InvalidDiffRequest):
        StateRef.revision(ARCH, 0)


@pytest.mark.parametrize(
    ("overrides", "field", "reason"),
    [
        ({"target": StateRef.revision(ARCH, 1)}, "target", "same_as_base"),
        ({"pricing_snapshot_id": uuid.uuid4()}, "pricing_snapshot_id", "needs_capacity_analysis"),
        ({"context": "x" * 2001}, "context", "too_long"),
        ({"requirement_ids": ("not-a-uuid",)}, "requirement_ids", "invalid"),
    ],
)
def test_invalid_requests_are_refused(overrides: dict[str, Any], field: str, reason: str) -> None:
    with pytest.raises(InvalidDiffRequest) as caught:
        request(**overrides)
    assert caught.value.details == {"field": field, "reason": reason}


def test_a_request_is_normalized() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    asked = request(requirement_ids=(second, first, second), context="  why  ")
    assert asked.requirement_ids == tuple(sorted({first, second}))
    assert asked.context == "why"
    assert request(context="   ").context is None


# --- changes -----------------------------------------------------------------------------------


def test_change_ids_are_derived_from_identity() -> None:
    assert modified().id == change_id(ElementType.NODE, "api")
    assert change_id(ElementType.NODE, "api") != change_id(ElementType.CONNECTION, "api")
    with pytest.raises(InvalidDiffRecord):
        modified(id="ch_forged")


def test_a_secret_never_carries_its_values() -> None:
    assert modified(fields=(a_secret(),), classes=(ChangeClass.SECURITY,)).secret
    with pytest.raises(InvalidDiffRecord):
        a_secret("old-key-value", ValueType.TEXT)


@pytest.mark.parametrize(
    "overrides",
    [
        {"fields": ()},  # a modification changes at least one field
        {"change": ChangeKind.ADDED},  # an addition has no field changes
        {"classes": ()},  # every change is classified
        {"renamed": True, "change": ChangeKind.REMOVED, "fields": ()},  # only a modification renames
        {"endpoints": ("api", "Bad Id!")},
    ],
)
def test_malformed_changes_are_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(InvalidDiffRecord):
        modified(**overrides)


def test_every_change_is_in_exactly_one_group() -> None:
    api, cache = modified(), added()
    one = ChangeGroup.of("shared_element", "API", "Same element.", (api,))
    other = ChangeGroup.of("added", "Cache", "Added.", (cache,))
    assert SemanticDiff(BASE_HASH, TARGET_HASH, (api, cache), (one, other)).groups == (one, other)
    with pytest.raises(InvalidDiffRecord):
        SemanticDiff(BASE_HASH, TARGET_HASH, (api, cache), (one,))  # a hidden change
    both = ChangeGroup.of("added", "All", "All.", (api, cache))
    with pytest.raises(InvalidDiffRecord):
        SemanticDiff(BASE_HASH, TARGET_HASH, (api, cache), (one, both))  # a change twice


def test_identical_states_have_no_changes() -> None:
    assert SemanticDiff(BASE_HASH, BASE_HASH).identical
    group = ChangeGroup.of("added", "C", "A.", (added(),))
    with pytest.raises(InvalidDiffRecord):
        SemanticDiff(BASE_HASH, BASE_HASH, (added(),), (group,))


def test_counts_are_counts_never_a_score() -> None:
    counts = semantic(modified(), added()).counts()
    assert counts["modified"] == counts["added"] == 1
    assert counts["class:scaling"] == 1
    assert "score" not in counts


def test_a_group_records_its_elements_and_never_its_own_facts() -> None:
    link = Change(
        change_id(ElementType.CONNECTION, "api-cache"),
        ElementType.CONNECTION,
        "api-cache",
        ChangeKind.ADDED,
        None,
        "request",
        (ChangeClass.TOPOLOGY,),
        endpoints=("api", "cache"),
    )
    group = ChangeGroup.of("connected_additions", "Cache", "Added together, connected.", (added(), link))
    assert group.element_ids == ("api", "api-cache", "cache")
    with pytest.raises(InvalidDiffRecord):
        ChangeGroup("cg_forged", "added", "x", "y", (link.id,), ())


# --- impacts -----------------------------------------------------------------------------------


def test_requirement_relations_need_their_basis() -> None:
    requirement = uuid.uuid4()
    relation = RequirementRelation.ELEMENT_CHANGED
    linked = RequirementImpact(
        requirement, "REQ-1", 2, "Latency", "p95", relation, ("api",), (modified().id,)
    )
    assert linked.to_dict()["relation"] == "element_changed"
    with pytest.raises(InvalidDiffRecord):
        RequirementImpact(requirement, "REQ-1", 2, "L", "S", relation)  # traced, but to no change
    with pytest.raises(InvalidDiffRecord):  # "potential" rests on a verdict that differs
        RequirementImpact(
            requirement,
            "REQ-1",
            2,
            "L",
            "S",
            RequirementRelation.POTENTIAL,
            base_verdict="met",
            target_verdict="met",
        )


def test_a_decision_may_require_review_and_nothing_more() -> None:
    impact = DecisionImpact(
        uuid.uuid4(), "ADR-14", "Use PostgreSQL", "accepted", ("db",), (modified("db").id,)
    )
    assert impact.to_dict()["note"].startswith("May require review")
    assert "invalid" not in str(impact.to_dict()).lower()


def test_engine_impacts_say_why_and_never_invent() -> None:
    introduced = FindingDelta("sec-1", FindingState.INTRODUCED, "high", "Public database")
    evaluated = EngineImpact("security", ImpactStatus.EVALUATED, findings=(introduced,), unchanged=3)
    assert evaluated.of_state(FindingState.INTRODUCED) == (introduced,)
    skipped = EngineImpact(
        "cost", ImpactStatus.NOT_EVALUATED, limitations=("No pricing snapshot was named.",)
    )
    assert skipped.measures == ()
    with pytest.raises(InvalidDiffRecord):
        EngineImpact("cost", ImpactStatus.NOT_EVALUATED)  # never silently skipped
    measured = (MeasureDelta("monthly_cost", "1", "2", "USD"),)
    with pytest.raises(InvalidDiffRecord):
        EngineImpact("cost", ImpactStatus.NOT_EVALUATED, measures=measured, limitations=("x",))
    with pytest.raises(InvalidDiffRecord):
        EngineImpact("security", ImpactStatus.FAILED)  # a failure has a code
    with pytest.raises(InvalidDiffRecord):
        FindingDelta("sec-2", FindingState.UNCHANGED, "low", "Counted, not listed")


# --- the stored diff ---------------------------------------------------------------------------


def test_a_diff_names_exactly_what_it_compared() -> None:
    assert not a_diff().identical
    with pytest.raises(InvalidDiffRecord):
        a_diff(semantic=SemanticDiff("c" * 64, TARGET_HASH))  # not the compared base
    with pytest.raises(InvalidDiffRecord):
        a_diff(base=ComparedState(StateRef.revision(ARCH, 7), BASE_HASH, "Other"))  # not the requested base


def test_impacts_cite_only_the_diffs_changes() -> None:
    stray = DecisionImpact(uuid.uuid4(), "ADR-1", "T", "accepted", ("x",), ("ch_not_here",))
    with pytest.raises(InvalidDiffRecord):
        a_diff(decisions=(stray,))
    twice = EngineImpact("security", ImpactStatus.EVALUATED)
    with pytest.raises(InvalidDiffRecord):
        a_diff(engines=(twice, twice))


# --- explanations ------------------------------------------------------------------------------


def test_a_statement_is_grounded_or_labelled_an_inference() -> None:
    grounded = Statement("Replicas went from 2 to 4.", (Grounding(Basis.CHANGE, modified().id),))
    assert grounded.refs(Basis.CHANGE) == (modified().id,)
    assert Statement("This may be for a traffic increase.", inferred=True).inferred
    with pytest.raises(InvalidDiffRecord):
        Statement("Performance improved.")  # neither grounded nor labelled


def test_an_explanation_walks_every_statement() -> None:
    fact = Statement("A cache was added.", (Grounding(Basis.CHANGE, added().id),))
    guess = Statement("Was the cache added to reduce database load?", inferred=True)
    explanation = DiffExplanation(
        fact, (GroupExplanation("cg_1", "Caching", fact, (guess,)),), questions=(guess,)
    )
    assert len(explanation.statements()) == 4
    with pytest.raises(InvalidDiffRecord):
        DiffExplanation(fact, (GroupExplanation("cg_1", "A", fact), GroupExplanation("cg_1", "B", fact)))


def test_an_explanation_run_says_what_happened() -> None:
    fact = Statement("A cache was added.", (Grounding(Basis.CHANGE, added().id),))
    done = ExplanationRun(
        uuid.uuid4(),
        uuid.uuid4(),
        ExplanationStatus.COMPLETED,
        USER,
        NOW,
        "scripted/model",
        "diff-explanation-v1",
        AgentUsage(model_calls=1),
        explanation=DiffExplanation(fact),
    )
    assert done.explanation is not None
    failure = ExplanationFailure.LLM_TIMEOUT
    failed = ExplanationRun(uuid.uuid4(), uuid.uuid4(), ExplanationStatus.FAILED, USER, NOW, failure=failure)
    assert failed.explanation is None
    quiet = ExplanationRun(uuid.uuid4(), uuid.uuid4(), ExplanationStatus.NOT_NEEDED, USER, NOW)
    assert quiet.usage.model_calls == 0
    with pytest.raises(InvalidDiffRecord):  # identical states: no model is called
        ExplanationRun(uuid.uuid4(), uuid.uuid4(), ExplanationStatus.NOT_NEEDED, USER, NOW, "m/x")
    with pytest.raises(InvalidDiffRecord):
        ExplanationRun(uuid.uuid4(), uuid.uuid4(), ExplanationStatus.COMPLETED, USER, NOW)  # no explanation
    with pytest.raises(InvalidDiffRecord):
        ExplanationRun(uuid.uuid4(), uuid.uuid4(), ExplanationStatus.FAILED, USER, NOW)  # no failure code
