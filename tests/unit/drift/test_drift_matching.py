"""Entity identity and matching (Drift Detection Engine, phase 3): baseline nodes matched to discovered
entities by stable source identifiers, then by identities a person confirmed — never by name;
conflicting claims and positional candidates left ambiguous; connections matched by id or endpoints
(a Service followed to its workload), told apart only by stated kinds; deterministic."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.domain.discovery.results import DiscoveryResult
from core.domain.discovery.runs import ArtifactInput, DiscoveryRequest
from core.domain.drift.errors import InvalidDriftRequest
from core.domain.drift.identity import IdentityMapping, current
from core.domain.drift.values import MatchMethod
from engines.discovery.engine import DeterministicDiscoveryEngine
from engines.drift.matching import Matching, match
from persistence.component_catalog import default_catalog
from tests.unit.discovery.test_discovery_sources import COMPOSE

ENGINE = DeterministicDiscoveryEngine(default_catalog())
AT = datetime(2026, 10, 2, tzinfo=UTC)
M = MatchMethod
DB, API, WEB = "compose:shop/service/db", "compose:shop/service/api", "compose:shop/service/web"
ORDERS, BILLING = "kubernetes:shop/deployment/orders", "kubernetes:shop/deployment/billing"
INGRESS = "kubernetes:shop/ingress/edge"
SHOP = "name: shop\nservices:\n  db:\n    image: postgres:16\n"
KUBE = """\
apiVersion: apps/v1
kind: Deployment
metadata: {name: NAME, namespace: shop}
spec:
  template:
    metadata: {labels: {app: shop}}
    spec: {containers: [{name: c, image: "postgres:16"}]}
---
apiVersion: v1
kind: Service
metadata: {name: front, namespace: shop}
spec: {selector: {app: shop}}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata: {name: edge, namespace: shop}
spec: {defaultBackend: {service: {name: front}}}
"""


def discover(*artifacts: tuple[str, str]) -> DiscoveryResult:
    return ENGINE.discover(DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts)))


def accepted(result: DiscoveryResult) -> ArchitectureIR:
    assert result.proposed is not None
    return result.proposed


def nodes(matching: Matching) -> dict[tuple[str | None, str | None], MatchMethod]:
    return {(m.baseline_id, m.discovered_key): m.method for m in matching.nodes}


def node(node_id: str, kind: NodeKind = NodeKind.SERVICE, provenance: Provenance | None = None) -> Node:
    return Node(node_id, kind, node_id.rsplit("/", 1)[-1], provenance=provenance)


def written(*connections: Connection, ids: tuple[str, ...] = (DB, API, WEB)) -> ArchitectureIR:
    return ArchitectureIR("Shop", nodes=tuple(node(i) for i in ids), connections=connections)


def test_a_stable_source_identifier_matches() -> None:
    matching = match(accepted(discover(("compose.yaml", SHOP))), discover(("compose.yaml", SHOP)), {})
    assert nodes(matching) == {(DB, DB): M.SAME_ID}


def test_a_rename_matches_only_through_a_confirmed_identity() -> None:
    baseline = accepted(discover(("compose.yaml", SHOP)))
    renamed = discover(("compose.yaml", SHOP.replace("db:", "database:")))
    database = "compose:shop/service/database"
    assert nodes(match(baseline, renamed, {})) == {(DB, None): M.UNMATCHED, (None, database): M.UNMATCHED}
    assert nodes(match(baseline, renamed, {DB: database})) == {(DB, database): M.CONFIRMED_MAPPING}


def test_similar_names_are_never_merged() -> None:
    baseline = accepted(discover(("compose.yaml", SHOP)))
    similar = discover(("compose.yaml", SHOP.replace("db:", "db-2:")))
    found = nodes(match(baseline, similar, {}))
    assert found == {(DB, None): M.UNMATCHED, (None, "compose:shop/service/db-2"): M.UNMATCHED}


def test_conflicting_claims_and_missing_mappings_stay_unresolved() -> None:
    run = discover(("compose.yaml", SHOP))
    conflict = match(written(ids=(DB, "legacy-db")), run, {"legacy-db": DB})  # an id and a mapping
    assert nodes(conflict) == {
        (DB, None): M.AMBIGUOUS, ("legacy-db", None): M.AMBIGUOUS, (None, DB): M.AMBIGUOUS,
    }  # fmt: skip
    lost = match(written(ids=("orders-db",)), run, {"orders-db": "compose:shop/service/gone"})
    [unmatched] = [m for m in lost.nodes if m.baseline_id == "orders-db"]
    assert unmatched.method is M.UNMATCHED
    assert (
        unmatched.reason
        == "The confirmed mapping names compose:shop/service/gone, which this run does not hold."
    )


def test_an_entity_where_a_node_was_discovered_is_a_candidate_not_a_match() -> None:
    place = Provenance(
        ProvenanceSource.KUBERNETES, "k8s/shop.yaml#0:metadata.name", actor="discovery:kubernetes"
    )
    baseline = ArchitectureIR("Shop", nodes=(node(ORDERS, provenance=place),))
    renamed = discover(("k8s/shop.yaml", KUBE.replace("NAME", "billing")))
    found = {m.baseline_id or m.discovered_key: m for m in match(baseline, renamed, {}).nodes}
    assert (found[ORDERS].method, found[ORDERS].candidates) == (M.AMBIGUOUS, (BILLING,))
    assert (found[BILLING].method, found[BILLING].candidates) == (M.AMBIGUOUS, (ORDERS,))  # neither merged


def test_connections_match_by_endpoints_through_services() -> None:
    data = Connection("c1", API, DB, ConnectionKind.DATA_ACCESS)
    matching = match(written(data), discover(("compose.yaml", COMPOSE)), {})
    [connected] = [c for c in matching.connections if c.baseline_id == "c1"]
    assert (connected.method, connected.source, connected.target) == (M.SIGNATURE, API, DB)
    added = [(c.source, c.target) for c in matching.connections if c.baseline_id is None]
    assert added == [(WEB, API)]  # depends_on: no baseline connection
    kube = discover(("k8s/shop.yaml", KUBE.replace("NAME", "orders")))
    routed = written(Connection("r1", INGRESS, ORDERS, ConnectionKind.REQUEST), ids=(INGRESS, ORDERS))
    [route] = [c for c in match(routed, kube, {}).connections if c.baseline_id == "r1"]
    assert route.method is M.SIGNATURE  # the Ingress reaches the Deployment through its Service


def test_parallel_connections_need_a_stated_kind() -> None:
    both = written(
        Connection("c1", API, DB, ConnectionKind.DATA_ACCESS),
        Connection("c2", API, DB, ConnectionKind.REQUEST),
    )
    found = {c.baseline_id: c for c in match(both, discover(("compose.yaml", COMPOSE)), {}).connections}
    assert {found["c1"].method, found["c2"].method} == {M.AMBIGUOUS}  # the host reference states no kind
    assert found["c1"].candidates == found["c2"].candidates


def test_the_same_relationship_id_matches_and_exclusions_are_left_out() -> None:
    run = discover(("compose.yaml", COMPOSE))
    host = next(r for r in run.relationships if r.source == API and r.reference == "host/db")
    kept = written(Connection(host.id, API, DB, ConnectionKind.DATA_ACCESS))
    [same] = [c for c in match(kept, run, {}).connections if c.baseline_id == host.id]
    assert same.method is M.SAME_ID
    excluded = match(kept, run, {}, excluded=(WEB, DB))
    assert {m.baseline_id for m in excluded.nodes} == {API}
    assert excluded.excluded == (DB, WEB)


def test_matching_is_deterministic() -> None:
    run = discover(("compose.yaml", COMPOSE))
    baseline = written(Connection("c1", API, DB, ConnectionKind.DATA_ACCESS))
    assert match(baseline, run, {}) == match(baseline, run, {})


def test_the_latest_confirmed_mapping_is_in_force_and_can_be_retracted() -> None:
    architecture, ada = uuid.UUID(int=1), uuid.UUID(int=2)
    first = IdentityMapping(architecture, "db", "compose:shop/service/db", ada, AT)
    moved = IdentityMapping(architecture, "db", "compose:shop/service/database", ada, AT + timedelta(days=1))
    retracted = IdentityMapping(
        architecture, "cache", None, ada, AT + timedelta(days=2), note="Not the same."
    )
    older = IdentityMapping(architecture, "cache", "compose:shop/service/redis", ada, AT)
    assert current((moved, first, retracted, older)) == {"db": "compose:shop/service/database"}
    with pytest.raises(InvalidDriftRequest):
        IdentityMapping(architecture, "../db", "x", ada, AT)
