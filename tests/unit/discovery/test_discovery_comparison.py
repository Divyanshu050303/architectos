"""Comparison and re-run support (Discovery Engine, phase 7): two results compared only on what both
read alike, comparability stated before any difference, no difference reported when versions differ;
a result compared with a baseline revision without claiming removals — what the sources do not
describe is unknown; stable identities for drift detection."""

import json
from dataclasses import replace

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.discovery.comparison import Change, Comparability, compare_results, compare_with_baseline
from core.domain.discovery.results import DiscoveryResult
from core.domain.discovery.runs import ArtifactInput, DiscoveryRequest
from engines.discovery.engine import DeterministicDiscoveryEngine
from persistence.component_catalog import default_catalog
from tests.unit.discovery.test_discovery_sources import COMPOSE, DEPLOYMENT

ENGINE = DeterministicDiscoveryEngine(default_catalog())
DB = "terraform:aws_db_instance.main"


def discover(*artifacts: tuple[str, str]) -> DiscoveryResult:
    return ENGINE.discover(DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts)))


def terraform(**attributes: object) -> str:
    return json.dumps({"resource": {"aws_db_instance": {"main": {"engine": "postgres", **attributes}}}})


def test_identical_inputs_are_comparable_and_identical() -> None:
    first, again = discover(("k8s/shop.yaml", DEPLOYMENT)), discover(("k8s/shop.yaml", DEPLOYMENT))
    comparison = compare_results(first, again)
    assert comparison.comparability is Comparability.COMPARABLE
    assert (comparison.identical, comparison.differences) == (True, ())
    assert first.sources_fingerprint == again.sources_fingerprint


def test_changed_declarations_are_reported_as_declared_differences() -> None:
    first = discover(("db.tf.json", terraform(instance_class="db.t3.micro")))
    later = discover(("db.tf.json", terraform(instance_class="db.r6g.large", allocated_storage=20)))
    comparison = compare_results(first, later)
    assert comparison.comparability is Comparability.COMPARABLE
    assert first.sources_fingerprint != later.sources_fingerprint
    [difference] = comparison.differences
    assert (difference.subject, difference.change) == (DB, Change.MODIFIED)
    assert {f.field: (f.before, f.after) for f in difference.fields} == {
        "configuration.instance_class": ("db.t3.micro", "db.r6g.large"),
        "configuration.storage_bytes": (None, 20 * 2**30),
    }


def test_added_and_removed_resources_and_references() -> None:
    changed = COMPOSE.replace("    depends_on: [api]\n", "").replace(
        "volumes:\n  data", "  cache:\n    image: redis:7\nvolumes:\n  data"
    )
    first, later = discover(("compose.yaml", COMPOSE)), discover(("compose.yaml", changed))
    changes = {(d.element, d.subject, d.change) for d in compare_results(first, later).differences}
    assert ("entity", "compose:shop/service/cache", Change.ADDED) in changes
    removed = ("relationship", "compose:shop/service/web -> compose:shop/service/api", Change.REMOVED)
    assert removed in changes


def test_different_parser_versions_are_not_comparable() -> None:
    first = discover(("k8s/shop.yaml", DEPLOYMENT))
    later = replace(first, extractors=dict(first.extractors) | {"kubernetes": 2})
    comparison = compare_results(first, later)
    assert comparison.comparability is Comparability.NOT_COMPARABLE
    assert comparison.differences == ()  # no drift is claimed
    assert [x.code for x in comparison.limitations] == ["versions_differ"]


def test_coverage_differences_narrow_the_comparison() -> None:
    first = discover(("k8s/shop.yaml", DEPLOYMENT), ("compose.yaml", COMPOSE))
    later = discover(("k8s/shop.yaml", DEPLOYMENT))
    comparison = compare_results(first, later)
    assert comparison.comparability is Comparability.PARTIALLY_COMPARABLE
    assert comparison.compared_artifacts == ("k8s/shop.yaml",)
    assert comparison.differences == ()  # the Compose services are not "removed": not compared
    assert "coverage_differs" in {x.code for x in comparison.limitations}


def test_unread_artifacts_are_not_compared() -> None:
    first = discover(("k8s/shop.yaml", DEPLOYMENT))
    later = discover(("k8s/shop.yaml", "kind: [unclosed\n"))
    comparison = compare_results(first, later)
    assert comparison.comparability is Comparability.NOT_COMPARABLE
    assert {x.code for x in comparison.limitations} >= {"not_read", "nothing_in_common"}
    assert comparison.differences == ()


def _baseline(*nodes: Node, connections: tuple[Connection, ...] = ()) -> ArchitectureIR:
    return ArchitectureIR("Shop", nodes=nodes, connections=connections)


def test_a_baseline_element_the_sources_do_not_describe_is_unknown_not_removed() -> None:
    result = discover(("db.tf.json", terraform(instance_class="db.t3.micro")))
    assert result.proposed is not None
    configured = Configuration({"instance_class": "db.r6g.large", "replicas": 2})
    baseline = _baseline(
        Node(DB, NodeKind.DATABASE, "main", configuration=configured),
        Node("api", NodeKind.SERVICE, "API"),
        connections=(Connection("c1", "api", DB, ConnectionKind.DATA_ACCESS),),
    )
    comparison = compare_with_baseline(result, result.proposed, baseline)
    assert comparison.comparability is Comparability.PARTIALLY_COMPARABLE  # sources are never complete
    found = {(d.element, d.subject): d for d in comparison.differences}
    assert found[("node", "api")].change is Change.NOT_IN_SOURCES
    assert found[("connection", f"api -[data_access]-> {DB}")].change is Change.NOT_IN_SOURCES
    fields = {f.field: f for f in found[("node", DB)].fields}
    instance = fields["configuration.instance_class"]
    assert (instance.change, instance.before, instance.after) == (
        Change.DIFFERS,
        "db.r6g.large",
        "db.t3.micro",
    )
    assert fields["configuration.replicas"].change is Change.NOT_IN_SOURCES  # not stated: unknown
    assert "declared_state_only" in {x.code for x in comparison.limitations}


def test_a_baseline_without_common_ids_is_not_compared_by_name() -> None:
    result = discover(("db.tf.json", terraform()))
    assert result.proposed is not None
    baseline = _baseline(Node("db", NodeKind.DATABASE, "main"))
    comparison = compare_with_baseline(result, result.proposed, baseline)
    assert comparison.comparability is Comparability.NOT_COMPARABLE
    assert comparison.differences == ()


def test_comparisons_are_deterministic_and_cite_both_sides() -> None:
    first = discover(("db.tf.json", terraform(instance_class="db.t3.micro")))
    later = discover(("db.tf.json", terraform(instance_class="db.r6g.large")))
    one, two = compare_results(first, later).to_dict(), compare_results(first, later).to_dict()
    assert one == two
    assert (one["earlier"], one["later"]) == (first.fingerprint, later.fingerprint)
