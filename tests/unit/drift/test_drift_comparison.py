"""Structural and configuration difference detection (Drift Detection Engine, phase 4): matched nodes
compared through the IR diff, typed and canonical; only what a source type can declare; values added,
changed and no longer declared kept apart; unreadable values unresolved; secrets never shown;
unmatched elements reported as candidates, not removals; deterministic."""

import json
from dataclasses import replace
from decimal import Decimal

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.discovery.results import DiscoveryResult
from core.domain.discovery.runs import ArtifactInput, DiscoveryRequest
from core.domain.drift.values import ElementType, FindingType
from engines.discovery.engine import DeterministicDiscoveryEngine
from engines.drift.comparison import Difference, differences, shown_values
from engines.drift.matching import match
from persistence.component_catalog import default_catalog
from tests.unit.discovery.test_discovery_sources import COMPOSE

ENGINE = DeterministicDiscoveryEngine(default_catalog())
F = FindingType
POSTGRES = "compose:shop/service/db"
API, WEB, OLD = "compose:shop/service/api", "compose:shop/service/web", "compose:shop/service/old"
SHOP = "name: shop\nservices:\n  db:\n    image: postgres:16\n"
LIMITED = SHOP + "    deploy: {resources: {limits: {cpus: '0.50'}}}\n"
LOCATION = "db.tf.json#0:resource.aws_db_instance.main"


def discover(*artifacts: tuple[str, str]) -> DiscoveryResult:
    return ENGINE.discover(DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts)))


def terraform(**attributes: object) -> str:
    return json.dumps({"resource": {"aws_db_instance": {"main": {"engine": "postgres", **attributes}}}})


def accepted(result: DiscoveryResult) -> ArchitectureIR:
    assert result.proposed is not None
    return result.proposed


def compared(baseline: ArchitectureIR, run: DiscoveryResult) -> tuple[Difference, ...]:
    return differences(baseline, run, match(baseline, run, {}))


def by_path(found: tuple[Difference, ...]) -> dict[str | None, Difference]:
    return {d.path: d for d in found}


def test_the_same_declarations_make_no_difference() -> None:
    source = discover(("db.tf.json", terraform(instance_class="db.t3.micro")))
    assert compared(accepted(source), discover(("db.tf.json", terraform(instance_class="db.t3.micro")))) == ()


def test_a_configuration_change_names_its_property_values_and_evidence() -> None:
    baseline = accepted(discover(("db.tf.json", terraform(instance_class="db.t3.micro"))))
    run = discover(("db.tf.json", terraform(instance_class="db.r6g.large", allocated_storage=20)))
    found = by_path(compared(baseline, run))
    changed = found["configuration.instance_class"]
    assert (changed.type, changed.baseline_value, changed.discovered_value) == (
        F.RESOURCE_CHANGED, "db.t3.micro", "db.r6g.large",
    )  # fmt: skip
    assert changed.evidence
    assert all(e.startswith("dsf_") for e in changed.evidence)
    assert (changed.locations, changed.baseline_reference) == ((LOCATION,), LOCATION)
    added = found["configuration.storage_bytes"]
    assert (added.baseline_value, added.discovered_value) == (None, 20 * 2**30)  # a value newly declared


def test_a_value_no_longer_declared_is_not_a_default() -> None:
    baseline = accepted(discover(("db.tf.json", terraform(instance_class="db.t3.micro"))))
    [gone] = compared(baseline, discover(("db.tf.json", terraform())))
    assert (gone.path, gone.baseline_value, gone.discovered_value) == (
        "configuration.instance_class", "db.t3.micro", None,
    )  # fmt: skip
    assert gone.notes == ("Not declared is not proof it is unset.",)


def test_values_are_compared_typed_and_only_where_the_source_can_declare_them() -> None:
    baseline = accepted(discover(("compose.yaml", LIMITED)))
    node = baseline.node(POSTGRES)
    assert node is not None
    values = dict(node.configuration.values) | {"cpu_limit_cores": Decimal("0.5"), "max_connections": 100}
    widened = ArchitectureIR("Shop", nodes=(replace(node, configuration=Configuration(values)),))
    assert (
        compared(widened, discover(("compose.yaml", LIMITED))) == ()
    )  # 0.50 is 0.5; max_connections: not declarable


def test_an_unreadable_declared_value_is_unresolved() -> None:
    baseline = accepted(discover(("db.tf.json", terraform(allocated_storage=20))))
    [unread] = compared(baseline, discover(("db.tf.json", terraform(allocated_storage="twenty"))))
    assert (unread.type, unread.path) == (F.UNRESOLVED_DIFFERENCE, "configuration.storage_bytes")
    assert unread.notes


def test_secret_values_are_never_kept() -> None:
    hidden = {"baseline_value": None, "discovered_value": None, "redacted": True}
    assert shown_values("configuration.api_token", "a", "b") == hidden
    assert shown_values("configuration.replicas", 1, 2) == {"baseline_value": 1, "discovered_value": 2}


def test_unmatched_elements_are_candidates_and_additions() -> None:
    baseline = accepted(discover(("compose.yaml", SHOP)))
    run = discover(("compose.yaml", "name: shop\nservices:\n  cache:\n    image: redis:7\n"))
    found = {d.subject: d for d in compared(baseline, run)}
    removed = found[f"node:{POSTGRES}"]
    assert (removed.type, removed.absent, removed.baseline_artifact) == (
        F.COMPONENT_REMOVED,
        True,
        "compose.yaml",
    )
    added = found["node:compose:shop/service/cache"]
    assert (added.type, added.notes) == (
        F.COMPONENT_ADDED,
        ("Its node kind is not established by the source.",),
    )


def test_kinds_and_components_are_compared_only_as_far_as_the_source_states_them() -> None:
    written = ArchitectureIR(
        "Shop", nodes=(Node(POSTGRES, NodeKind.CACHE, "db", component="databases/mysql"),)
    )
    found = by_path(compared(written, discover(("compose.yaml", SHOP))))
    assert (found["kind"].type, found["kind"].inferred) == (F.COMPONENT_MODIFIED, True)  # from the catalog
    mapping = found["component"]
    assert (mapping.type, mapping.discovered_value, mapping.inferred) == (
        F.MAPPING_CHANGED, "databases/postgresql", True,
    )  # fmt: skip


def test_connection_kinds_are_compared_only_when_stated() -> None:
    nodes = tuple(Node(i, NodeKind.SERVICE, i.rsplit("/", 1)[-1]) for i in (API, WEB, POSTGRES, OLD))
    baseline = ArchitectureIR(
        "Shop", nodes=nodes,
        connections=(
            Connection("c1", WEB, API, ConnectionKind.REQUEST),  # the source states depends_on
            Connection("c2", API, POSTGRES, ConnectionKind.DATA_ACCESS),  # the source states no kind
            Connection("c3", API, OLD, ConnectionKind.REQUEST),
        ),
    )  # fmt: skip
    connections = [
        d
        for d in compared(baseline, discover(("compose.yaml", COMPOSE)))
        if d.element is ElementType.CONNECTION
    ]
    found = {d.subject: d for d in connections}
    modified = found["connection:c1"]
    assert (modified.type, modified.baseline_value, modified.discovered_value) == (
        F.CONNECTION_MODIFIED, "request", "dependency",
    )  # fmt: skip
    assert "connection:c2" not in found  # no kind stated: nothing compared
    removed = found["connection:c3"]
    assert (removed.type, removed.absent) == (F.CONNECTION_REMOVED, True)
    assert removed.notes == (f"Its endpoint {OLD} has no match in this run.",)


def test_differences_are_deterministic() -> None:
    baseline = accepted(discover(("db.tf.json", terraform(instance_class="db.t3.micro"))))
    run = discover(("db.tf.json", terraform(instance_class="db.r6g.large")))
    assert compared(baseline, run) == compared(baseline, run)
