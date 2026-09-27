"""Decision and ADR integration (Milestone 13, phase 8): a proposed ADR drafted from an evolution
analysis; the human decision explicit (who, which option, why); proposal and accepted decision
distinct; accepting never changes the architecture; the resulting revision linked only by a person,
after a separate change; stable references."""

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from core.architecture_ir.serialization import to_dict
from core.domain.decisions.adr import decision_ref, draft_from_evolution, to_markdown
from core.domain.decisions.entities import Decision, DecisionStatus
from core.domain.decisions.errors import InvalidDecision, InvalidDecisionTransition
from core.domain.engine_results import Evidence
from core.domain.evolution.candidates import Candidate, RuleRef
from core.domain.evolution.results import EvolutionResult
from core.domain.evolution.tradeoffs import with_tradeoffs
from core.domain.evolution.values import ValidationState
from core.domain.simulations.scenarios import ConfigurationChange
from engines.evolution.rulebook import default_registry
from tests.unit.evolution.test_evolution_tradeoffs import WORKLOAD_GOAL, scaled
from tests.unit.evolution.test_evolution_triggers import BASELINE, IR

AT = datetime(2026, 9, 27, 12, tzinfo=UTC)
ANALYSIS = uuid.UUID(int=21)
PROJECT = uuid.UUID(int=22)
ADA, VIC = uuid.UUID(int=31), uuid.UUID(int=32)


def alternatives() -> tuple[Candidate, Candidate]:
    horizontal = scaled()
    vertical = with_tradeoffs(
        replace(
            horizontal,
            rule=RuleRef("scale-cpu", 1),
            changes=(ConfigurationChange("api", "cpu_limit_cores", 2),),
            impacts=(),
        ),
        (WORKLOAD_GOAL,),
    )
    return horizontal, vertical


def result() -> EvolutionResult:
    return EvolutionResult(
        BASELINE,
        default_registry().model_set(),
        (WORKLOAD_GOAL,),
        alternatives(),
        assumptions=(Evidence("peak", "Peak lasts two hours."),),
    )


def draft(**fields: object) -> Decision:
    values: dict[str, object] = {
        "decision_id": uuid.UUID(int=41), "project_id": PROJECT, "number": 3, "analysis_id": ANALYSIS,
        "result": result(), "created_by": ADA, "at": AT,
    }  # fmt: skip
    return draft_from_evolution(**(values | fields))  # type: ignore[arg-type]


def test_a_proposed_adr_is_drafted_from_an_analysis_without_choosing() -> None:
    evolution = result()
    decision = draft()
    assert (decision.status, decision.reference, decision.chosen_option) == (
        DecisionStatus.PROPOSED,
        "ADR-3",
        None,
    )
    assert [o.candidate_id for o in decision.options] == sorted(c.id for c in evolution.candidates)
    assert decision.source is not None
    assert (decision.source.analysis_id, decision.source.baseline) == (ANALYSIS, BASELINE)
    assert decision.source.model_version == evolution.model_set.version
    assert decision.goals == (WORKLOAD_GOAL.key,)
    assert "the engine prefers none" in decision.context
    assert decision.evidence
    assert decision.assumptions == (Evidence("peak", "Peak lasts two hours."),)
    assert decision.related_element_ids == ("api",)
    horizontal = decision.option(alternatives()[0].id)
    assert horizontal is not None
    assert ("capacity", "improves") in horizontal.consequences  # each option keeps its trade-offs
    assert horizontal.changes == ("api.replicas = 4",)


def test_options_can_be_chosen_and_must_exist() -> None:
    evolution = result()
    one = evolution.candidates[0].id
    assert [o.candidate_id for o in draft(candidate_ids=(one,)).options] == [one]
    with pytest.raises(InvalidDecision):
        draft(candidate_ids=("evo_" + "0" * 20,))


def test_a_person_accepts_one_option_and_nothing_else_changes() -> None:
    before = json.dumps(to_dict(IR), sort_keys=True)
    proposed = draft()
    chosen = proposed.options[1].candidate_id
    accepted = proposed.accept(chosen, "Scaling out fits the team's operations.", VIC, AT)
    assert (accepted.status, accepted.chosen_option, accepted.decided_by_user_id) == (
        DecisionStatus.ACCEPTED, chosen, VIC,
    )  # fmt: skip
    assert accepted.rationale == "Scaling out fits the team's operations."
    assert accepted.resulting_revision is None  # accepting applies nothing
    assert json.dumps(to_dict(IR), sort_keys=True) == before
    assert proposed.status is DecisionStatus.PROPOSED  # the proposal is a distinct record state
    with pytest.raises(InvalidDecision):
        proposed.accept("evo_" + "1" * 20, "Not an option.", VIC, AT)
    with pytest.raises(InvalidDecision):
        proposed.accept(chosen, "  ", VIC, AT)  # a decision needs a rationale


def test_an_option_validation_refused_cannot_be_accepted() -> None:
    evolution = result()
    invalid = replace(evolution.candidates[0], validation=ValidationState.INVALID)
    decision = draft(result=replace(evolution, candidates=(invalid,)))
    with pytest.raises(InvalidDecision) as refused:
        decision.accept(invalid.id, "Anyway.", VIC, AT)
    assert refused.value.details["reason"] == "option_invalid"


def test_the_lifecycle_is_explicit() -> None:
    proposed = draft()
    rejected = proposed.reject("Neither fits the budget.", VIC, AT)
    assert (rejected.status, rejected.chosen_option, rejected.decided_by_user_id) == (
        DecisionStatus.REJECTED,
        None,
        VIC,
    )
    with pytest.raises(InvalidDecisionTransition):
        rejected.accept(proposed.options[0].candidate_id, "Changed my mind.", VIC, AT)
    accepted = proposed.accept(proposed.options[0].candidate_id, "Fits.", VIC, AT)
    with pytest.raises(InvalidDecisionTransition):
        accepted.reject("Too late.", VIC, AT)
    with pytest.raises(InvalidDecisionTransition):
        proposed.supersede(uuid.UUID(int=99))  # only an accepted decision is superseded
    with pytest.raises(InvalidDecision):
        accepted.supersede(accepted.id)
    superseded = accepted.supersede(uuid.UUID(int=99))
    assert (superseded.status, superseded.superseded_by) == (DecisionStatus.SUPERSEDED, uuid.UUID(int=99))


def test_a_resulting_revision_is_linked_only_by_a_person_after_the_change() -> None:
    proposed = draft()
    with pytest.raises(InvalidDecisionTransition):
        proposed.link_revision(2, "c" * 64, VIC, AT)  # nothing decided yet
    accepted = proposed.accept(proposed.options[0].candidate_id, "Fits.", VIC, AT)
    with pytest.raises(InvalidDecision):
        accepted.link_revision(BASELINE.revision_number, "c" * 64, VIC, AT)  # not after the baseline
    linked = accepted.link_revision(2, "c" * 64, ADA, AT)
    assert linked.resulting_revision is not None
    assert (linked.resulting_revision.number, linked.resulting_revision.linked_by_user_id) == (2, ADA)
    with pytest.raises(InvalidDecision):
        linked.link_revision(3, "d" * 64, ADA, AT)  # once


def test_the_adr_document_and_references_are_stable() -> None:
    evolution = result()
    proposed = draft(result=evolution)
    text = to_markdown(proposed)
    assert text == to_markdown(draft(result=evolution))  # the same analysis, the same document
    for heading in ("# ADR-3:", "## Context", "## Options considered", "## Decision", "## Consequences",
                    "## Evidence", "## Assumptions", "## Resulting revision"):  # fmt: skip
        assert heading in text
    assert "Not decided. The options above are proposals for engineering review." in text
    assert "Not linked. Applying a decision is a separate, authorized change" in text
    chosen = proposed.options[0]
    accepted = to_markdown(proposed.accept(chosen.candidate_id, "Fits.", VIC, AT))
    assert f"Accepted: {chosen.title} (`{chosen.candidate_id}`). Rationale: Fits." in accepted
    assert "(chosen)" in accepted
    ref = decision_ref(proposed)
    assert (ref.decision_id, ref.subject_ids) == (proposed.id, ("api",))
    assert not {"score", "winner", "best"} & set(text.lower().replace("#", " ").split())
