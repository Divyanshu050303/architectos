"""Requirement and decision impact (spec 10, 11): only through traces and decision records, each
relation for its stated reason, never "violated" or "invalid" unless an engine says so."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.traceability import DecisionRef, RequirementRef
from core.domain.architecture_diff.impacts import RequirementImpact
from core.domain.architecture_diff.values import RequirementRelation
from core.domain.decisions.entities import Decision, DecisionStatus
from core.domain.requirements.entities import Requirement
from engines.architecture_diff.semantic import semantic_diff
from engines.architecture_diff.traceability import decision_impacts, requirement_impacts, traceability
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.requirements.test_planning_input import requirement

NOW = datetime(2026, 10, 3, tzinfo=UTC)
R = RequirementRelation
LATENCY, AVAILABILITY, AUDIT = requirement(1), requirement(2, version=3), requirement(3)
REQUIREMENTS = {r.id: r for r in (LATENCY, AVAILABILITY, AUDIT)}
SCALED = {"configuration": Configuration({"replicas": 4})}


def ref(r: Requirement, version: int | None = None) -> RequirementRef:
    return RequirementRef(r.id, version if version is not None else r.version)


def shop(**node_fields: dict[str, Any]) -> ArchitectureIR:
    api = node(
        "api",
        name="Orders API",
        requirement_refs=(ref(LATENCY),),
        configuration=Configuration({"replicas": 2}),
    )
    db = node("db", NodeKind.DATABASE, name="Orders DB", requirement_refs=(ref(AVAILABILITY),))
    nodes = tuple(replace(n, **node_fields.get(n.id, {})) for n in (api, db))
    return ArchitectureIR("Shop", nodes=nodes, connections=(connection("api-db", "api", "db"),))


def impacts(base: ArchitectureIR, target: ArchitectureIR, **kwargs: Any) -> dict[str, RequirementImpact]:
    found, _ = requirement_impacts(base, target, semantic_diff(base, target), REQUIREMENTS, **kwargs)
    return {i.reference: i for i in found}


def adr(number: int, elements: tuple[str, ...], status: DecisionStatus = DecisionStatus.ACCEPTED) -> Decision:
    return Decision(
        id=uuid.UUID(int=500 + number),
        project_id=uuid.uuid4(),
        architecture_id=uuid.uuid4(),
        number=number,
        title=f"Decision {number}",
        status=status,
        context="Why.",
        options=(),
        created_by_user_id=None,
        created_at=NOW,
        related_element_ids=elements,
    )


# --- requirements ------------------------------------------------------------------------------


def test_an_element_traced_to_a_requirement_changed() -> None:
    found = impacts(shop(), shop(api=SCALED))
    assert set(found) == {"REQ-1"}
    impact = found["REQ-1"]
    assert impact.relation is R.ELEMENT_CHANGED
    assert impact.element_ids == ("api",)
    assert impact.statement == LATENCY.content.statement
    assert impact.version == LATENCY.version


def test_a_trace_added_or_removed_is_a_direct_change() -> None:
    added = impacts(shop(), shop(api={"requirement_refs": (ref(LATENCY), ref(AUDIT))}))
    assert added["REQ-3"].relation is R.DIRECTLY_CHANGED
    assert added["REQ-1"].relation is R.ELEMENT_CHANGED  # the same element changed, its trace did not
    removed = impacts(shop(), shop(db={"requirement_refs": ()}))
    assert removed["REQ-2"].relation is R.DIRECTLY_CHANGED


def test_a_trace_to_another_version_is_a_direct_change() -> None:
    found = impacts(shop(), shop(db={"requirement_refs": (ref(AVAILABILITY, 2),)}))
    assert found["REQ-2"].relation is R.DIRECTLY_CHANGED


def test_a_removed_element_takes_its_traces_with_it() -> None:
    base = shop()
    target = replace(base, nodes=(base.nodes[0],), connections=())
    found = impacts(base, target)
    assert found["REQ-2"].relation is R.DIRECTLY_CHANGED
    assert found["REQ-2"].element_ids == ("db",)


def test_a_differing_verdict_is_only_potential() -> None:
    verdicts = ({AUDIT.id: "satisfied"}, {AUDIT.id: "violated"})
    audit = impacts(shop(), shop(api={"description": "x"}), verdicts=verdicts)["REQ-3"]
    assert audit.relation is R.POTENTIAL
    assert (audit.base_verdict, audit.target_verdict) == ("satisfied", "violated")  # as the engine said
    assert audit.change_ids == ()


def test_unrelated_requirements_are_listed_only_when_scoped() -> None:
    base, target = shop(), shop(api=SCALED)
    assert "REQ-3" not in impacts(base, target)
    same = ({AUDIT.id: "satisfied"}, {AUDIT.id: "satisfied"})
    scoped = impacts(base, target, scope=(AUDIT.id, LATENCY.id), verdicts=same)
    assert scoped["REQ-3"].relation is R.NO_RELATIONSHIP
    assert scoped["REQ-1"].relation is R.ELEMENT_CHANGED
    assert "REQ-2" not in scoped  # outside the scope
    unknown = impacts(base, target, scope=(AUDIT.id,))
    assert unknown["REQ-3"].relation is R.UNDETERMINED  # no verdict: cannot be determined


def test_an_unreadable_requirement_is_an_unknown_never_invented() -> None:
    ghost = uuid.uuid4()
    target = shop(api={"requirement_refs": (ref(LATENCY), RequirementRef(ghost, 1))})
    found, unknowns = requirement_impacts(shop(), target, semantic_diff(shop(), target), REQUIREMENTS)
    assert ghost not in {i.requirement_id for i in found}
    assert unknowns == (f"Requirement {ghost} is referenced but cannot be read here.",)


def test_identical_states_touch_nothing() -> None:
    assert impacts(shop(), shop()) == {}


def test_every_impact_cites_the_changes_it_rests_on() -> None:
    base, target = shop(), shop(api={"requirement_refs": (ref(AUDIT),)})
    semantic = semantic_diff(base, target)
    found, _ = requirement_impacts(base, target, semantic, REQUIREMENTS)
    ids = {c.id for c in semantic.changes}
    assert found
    assert all(set(i.change_ids) <= ids for i in found)
    order = list(R)
    assert [i.relation for i in found] == sorted((i.relation for i in found), key=order.index)


# --- decisions ---------------------------------------------------------------------------------


def test_a_decision_whose_element_changed_may_require_review() -> None:
    base, target = shop(), shop(db={"name": "Orders store"})
    found = decision_impacts(base, target, semantic_diff(base, target), [adr(14, ("db",)), adr(2, ("api",))])
    assert [d.reference for d in found] == ["ADR-14"]
    assert found[0].to_dict()["note"] == "May require review: an element it concerns changed."


def test_decisions_not_in_force_are_not_raised() -> None:
    base, target = shop(), shop(db={"name": "Orders store"})
    semantic = semantic_diff(base, target)
    old = [adr(1, ("db",), DecisionStatus.REJECTED), adr(2, ("db",), DecisionStatus.SUPERSEDED)]
    assert decision_impacts(base, target, semantic, old) == ()
    assert decision_impacts(base, target, semantic, [adr(3, ("db",), DecisionStatus.PROPOSED)])


def test_the_architectures_own_decision_references_count() -> None:
    decision = adr(7, ())
    base = replace(shop(), decisions=(DecisionRef(decision.id, ("api",)),))
    target = replace(shop(api={"configuration": Configuration({"replicas": 3})}), decisions=base.decisions)
    [found] = decision_impacts(base, target, semantic_diff(base, target), [decision])
    assert found.element_ids == ("api",)


def test_traceability_together() -> None:
    base, target = shop(), shop(api=SCALED)
    result = traceability(base, target, semantic_diff(base, target), REQUIREMENTS, [adr(1, ("api",))])
    assert [i.reference for i in result.requirements] == ["REQ-1"]
    assert [d.reference for d in result.decisions] == ["ADR-1"]
    assert result.unknowns == ()
