"""Change analysis (Migration Planning, phase 2): the Architecture IR's own diff of the exact source and
target, classified — migration-relevant or metadata-only, what each change touches and which engines
read it, and what needs a person's interpretation or cannot be planned — for a later revision or an
evolution candidate on the source; neither architecture is modified."""

import dataclasses
import json
import uuid
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.diff import diff
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.evolution.candidates import BaselineRef, Candidate, EvidenceRef, RuleRef
from core.domain.evolution.values import CandidateCategory, EvidenceSource, EvidenceState, ValidationState
from core.domain.migrations.changes import Aspect, ChangeAnalysis, Interpretation, MigrationChange, Relevance
from core.domain.migrations.errors import InvalidMigrationRequest
from core.domain.migrations.plans import SourceRef
from core.domain.migrations.values import FindingType, TargetKind
from core.domain.simulations.scenarios import ConfigurationChange
from engines.migration.changes import analyze, candidate_target, revision_target
from tests.unit.architecture_ir.builders import connection, node

ARCHITECTURE = uuid.UUID(int=7)


def shop(**nodes: Node) -> ArchitectureIR:
    """web → api → db, with the named nodes replaced or added."""
    db_values: dict[str, Any] = {"replicas": 1, "region": "eu-west-1", "encryption_at_rest": False}
    base = {
        "web": node("web", NodeKind.CLIENT),
        "api": node("api", configuration=Configuration({"replicas": 2, "logs": True})),
        "db": node("db", NodeKind.DATABASE, technology=Technology("postgresql", "16"),
                   configuration=Configuration(db_values)),
    }  # fmt: skip
    connections = (
        connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https"),
        connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql"),
    )
    return ArchitectureIR("Shop", nodes=tuple((base | nodes).values()), connections=connections)


SOURCE_IR = shop()
SOURCE = SourceRef(ARCHITECTURE, 1, content_hash(SOURCE_IR))


def original(node_id: str) -> Node:
    return next(n for n in SOURCE_IR.nodes if n.id == node_id)


def changed(node_id: str, **values: Any) -> Node:
    current = original(node_id)
    return dataclasses.replace(
        current, configuration=Configuration({**current.configuration.values, **values})
    )


def run(after: ArchitectureIR) -> ChangeAnalysis:
    return analyze(SOURCE_IR, after, SOURCE, revision_target(SOURCE, 2, content_hash(after)))


def change_of(analysis: ChangeAnalysis, ref: str) -> MigrationChange:
    return analysis.by_ref()[ref]


def test_the_ir_diff_is_reused_as_it_is() -> None:
    after = shop(api=changed("api", replicas=4))
    analysis = run(after)
    assert analysis.diff_summary == diff(SOURCE_IR, after).summary()
    [api] = analysis.relevant
    assert api.fields == diff(SOURCE_IR, after).nodes[0].fields  # the diff's own field changes


def test_changes_are_classified_by_what_they_touch_and_which_engines_read_it() -> None:
    cache = node("cache", NodeKind.CACHE, configuration=Configuration({"replicas": 1}))
    nodes = shop(
        api=changed("api", replicas=4, logs=False), db=changed("db", encryption_at_rest=True), cache=cache
    )
    after = dataclasses.replace(
        nodes, connections=(*SOURCE_IR.connections, connection("api-cache", "api", "cache", protocol="redis"))
    )
    analysis = run(after)
    api = change_of(analysis, "node:api:modified")
    assert {Aspect.RESOURCES, Aspect.SCALING, Aspect.OBSERVABILITY} <= set(api.aspects)
    assert {"capacity", "cost", "reliability", "observability"} <= set(api.engines)
    db = change_of(analysis, "node:db:modified")
    assert Aspect.SECURITY in db.aspects
    assert (db.engines, db.stateful) == (("security",), True)
    added = change_of(analysis, "node:cache:added")
    assert set(added.aspects) == {Aspect.PROVISIONING, Aspect.TOPOLOGY}
    assert added.interpretation is Interpretation.SUPPORTED
    assert set(change_of(analysis, "connection:api-cache:added").aspects) >= {
        Aspect.ROUTING,
        Aspect.PROVISIONING,
    }
    assert analysis.findings == ()  # everything here can be planned


def test_metadata_only_changes_are_recorded_but_need_no_step() -> None:
    described = dataclasses.replace(original("api"), description="The API.")
    analysis = run(dataclasses.replace(shop(api=described), description="The shop."))
    assert {c.relevance for c in analysis.changes} == {Relevance.METADATA}
    assert analysis.relevant == ()
    [finding] = analysis.findings
    assert finding.type is FindingType.NO_CHANGES


def test_what_is_not_modeled_needs_interpretation_and_a_role_change_is_unsupported() -> None:
    after = shop(
        db=changed("db", region="us-east-1"),  # a stateful component moving region
        api=dataclasses.replace(original("api"), kind=NodeKind.WORKER),  # a change of role
        group=node("group", NodeKind.BOUNDARY),  # a grouping
    )
    analysis = run(after)
    assert {f.key: f.type for f in analysis.findings} == {
        "node:api:modified": FindingType.UNSUPPORTED_CHANGE,
        "node:db:modified": FindingType.MANUAL_INTERPRETATION,
        "node:group:added": FindingType.MANUAL_INTERPRETATION,
    }
    assert "moving its data is not modeled" in (change_of(analysis, "node:db:modified").reason or "")
    assert run(shop(api=changed("api", region="us-east-1"))).findings == ()  # a stateless move can be planned


def test_unrecognized_settings_and_unknown_values_are_for_a_person() -> None:
    api = original("api")
    vendor = dataclasses.replace(
        api, configuration=Configuration(api.configuration.values, extra={"vendor_flag": "x"})
    )
    unknown = dataclasses.replace(
        api, configuration=Configuration({"logs": True}, unknown=frozenset({"replicas"}))
    )
    for after in (shop(api=vendor), shop(api=unknown)):
        [finding] = run(after).findings
        assert finding.type is FindingType.MANUAL_INTERPRETATION


def test_a_technology_change_of_a_stateful_component_is_marked() -> None:
    db = dataclasses.replace(original("db"), technology=Technology("mysql", "8"))
    change = change_of(run(shop(db=db)), "node:db:modified")
    assert Aspect.TECHNOLOGY in change.aspects
    assert change.stateful


def test_the_contents_must_be_the_referenced_revisions() -> None:
    after = shop(api=changed("api", replicas=4))
    with pytest.raises(InvalidMigrationRequest) as error:
        analyze(SOURCE_IR, after, SOURCE, revision_target(SOURCE, 2, "0" * 64))
    assert error.value.details == {"field": "target", "reason": "content_does_not_match"}


def candidate(*changes: ConfigurationChange, baseline: BaselineRef | None = None, **fields: Any) -> Candidate:
    evidence = EvidenceRef(EvidenceSource.CAPACITY, "a1", EvidenceState.CURRENT, None, 1, SOURCE.content_hash)
    return Candidate(
        RuleRef("scale-replicas", 1),
        baseline or BaselineRef(ARCHITECTURE, 1, SOURCE.content_hash),
        CandidateCategory.SCALING,
        "Scale api",
        "More replicas.",
        changes,
        ("goal",),
        "Stated.",
        (evidence,),
        **fields,
    )


def test_a_candidate_target_is_its_overlay_on_the_source_revision() -> None:
    proposed = candidate(ConfigurationChange("api", "replicas", 4))
    target, ir = candidate_target(SOURCE, SOURCE_IR, uuid.UUID(int=9), proposed)
    assert (target.kind, target.revision_number, target.candidate_id) == (
        TargetKind.CANDIDATE,
        1,
        proposed.id,
    )
    assert target.content_hash == content_hash(ir)
    assert target.content_hash != SOURCE.content_hash
    [api] = analyze(SOURCE_IR, ir, SOURCE, target).relevant
    assert api.changed("configuration.replicas")[0].after == 4


SCALE_API = ConfigurationChange("api", "replicas", 4)
REFUSED = [
    (candidate(SCALE_API, baseline=BaselineRef(ARCHITECTURE, 2, "3" * 64)), "not_on_the_source"),
    (candidate(SCALE_API, validation=ValidationState.INVALID), "candidate_not_valid"),
    (candidate(ConfigurationChange("cache", "replicas", 4)), "candidate_not_applicable"),
]


@pytest.mark.parametrize(("proposed", "reason"), REFUSED)
def test_a_candidate_on_another_baseline_or_refused_by_validation_is_refused(
    proposed: Candidate, reason: str
) -> None:
    with pytest.raises(InvalidMigrationRequest) as error:
        candidate_target(SOURCE, SOURCE_IR, uuid.UUID(int=9), proposed)
    assert error.value.details == {"field": "target.candidate_id", "reason": reason}


def test_analysis_is_deterministic_and_changes_neither_architecture() -> None:
    after = shop(api=changed("api", replicas=4), db=changed("db", encryption_at_rest=True))
    before_source, before_target = json.dumps(to_dict(SOURCE_IR)), json.dumps(to_dict(after))
    first, again = run(after), run(after)
    assert first.to_dict() == again.to_dict()
    assert [c.ref for c in first.changes] == sorted(c.ref for c in first.changes)
    assert (json.dumps(to_dict(SOURCE_IR)), json.dumps(to_dict(after))) == (before_source, before_target)
