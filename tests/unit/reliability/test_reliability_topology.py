"""Topology and dependency analysis (Milestone 9, phase 3): request paths from explicit connection
semantics, series availability, and topology findings, never guessed."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.results import Certainty, Estimate, Source
from core.domain.capacity.units import Quantity
from core.domain.engine_results import Evidence
from core.domain.reliability.analyses import Objective, ReliabilityAnalysisRequest
from core.domain.reliability.errors import InvalidReliabilityRequest
from core.domain.reliability.results import FindingType, ObjectiveKind, ReliabilityFinding
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.reliability.context import ReliabilityContext
from engines.reliability.dependency import RequestPaths
from engines.reliability.engine import ModelInputs, ModelMeta, ModelOutput, Registry, analyze
from engines.reliability.spof import TopologyFindings
from tests.unit.architecture_ir.builders import connection, node

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
T = FindingType
SYNC = {"kind": ConnectionKind.REQUEST, "protocol": "https", "interaction": Interaction.SYNCHRONOUS}


class Declared:
    """Stand-in: the declared whole-component availability."""

    meta = ModelMeta(
        "declared", 1, "Declared", "Stand-in.", frozenset(NodeKind) - {NodeKind.CLIENT, NodeKind.BOUNDARY},
        ("availability",), ("availability",), formula="configuration.availability",
    )  # fmt: skip

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        value = inputs.facts.number("availability")
        quantity = Quantity.of(value, "ratio")
        return ModelOutput(
            (
                Estimate(
                    inputs.node.id, "availability", quantity, Source.DECLARED, "configuration.availability"
                ),
            )
        )


def comp(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Any:
    return node(node_id, kind, configuration=Configuration(values))


def call(source: str, target: str, **overrides: Any) -> Any:
    return connection(f"{source}-{target}", source, target, **(SYNC | overrides))


def data(source: str, target: str, **overrides: Any) -> Any:
    return call(source, target, kind=ConnectionKind.DATA_ACCESS, protocol="postgresql", **overrides)


def run(nodes: tuple[Any, ...], links: tuple[Any, ...] = (), **request: Any) -> Any:
    ir = ArchitectureIR("Shop", nodes=nodes, connections=links)
    context = ReliabilityContext(ir, REVISION, ReliabilityAnalysisRequest(uuid.UUID(int=1), 1, **request))
    return analyze(context, Registry([Declared()], [RequestPaths(), TopologyFindings()]))


def findings(result: Any, kind: FindingType) -> list[ReliabilityFinding]:
    return [f for f in result.findings if f.type is kind]


WEB = node("web", NodeKind.CLIENT)
SOLID: dict[str, Any] = {"replicas": 3, "min_healthy_replicas": 2, "availability_zones": ("a", "b", "c"),
         "failure_independence": "independent", "failover_mode": "automatic", "failover_seconds": 30,
         "mttr_seconds": 600}  # fmt: skip


# --- paths ---------------------------------------------------------------------------------------


def test_a_single_dependency() -> None:
    result = run((WEB, comp("api", replicas=1, availability=Decimal("0.99"))), (call("web", "api"),))
    [path] = result.paths
    assert (path.entry_id, path.node_ids, path.connection_ids) == ("web", ("web", "api"), ("web-api",))
    assert path.availability.quantity == Quantity.of("0.99", "ratio")
    [spof] = findings(result, T.SINGLE_POINT_OF_FAILURE)
    assert (spof.node_ids, spof.severity, spof.certainty) == (("api",), Severity.HIGH, Certainty.MODELED)


def test_series_dependencies_multiply_and_say_what_they_assume() -> None:
    nodes = (
        WEB,
        comp("lb", NodeKind.LOAD_BALANCER, availability=Decimal("0.9999")),
        comp("api", availability=Decimal("0.999")),
        comp("db", NodeKind.DATABASE, availability=Decimal("0.9995")),
    )
    links = (
        call("web", "lb"),
        call("lb", "api"),
        data("api", "db"),
    )
    [path] = run(nodes, links).paths
    assert path.node_ids == ("web", "lb", "api", "db")
    assert path.availability.quantity == Quantity.of(
        "0.998400650", "ratio"
    )  # 0.9999 x 0.999 x 0.9995 = 0.99840064995
    assert path.availability.source is Source.MODEL_ESTIMATE
    assert Evidence("api.availability", "0.999") in path.availability.inputs
    assert "independent" in path.availability.inputs[0].value


def test_a_path_is_unknown_while_any_required_component_is() -> None:
    nodes = (WEB, comp("api", availability=Decimal("0.999")), comp("db", NodeKind.DATABASE))
    links = (call("web", "api"), data("api", "db"))
    [path] = run(nodes, links).paths
    assert (path.availability.quantity, path.availability.missing, path.complete) == (
        None,
        ("db.availability",),
        False,
    )


def test_only_explicitly_required_connections_are_followed() -> None:
    nodes = (
        WEB, comp("api", availability=Decimal("0.999")), comp("bus", NodeKind.QUEUE),
        comp("worker", NodeKind.WORKER),
        comp("cache", NodeKind.CACHE), comp("audit"), comp("replica", NodeKind.DATABASE),
        comp("db", NodeKind.DATABASE), comp("vault", NodeKind.EXTERNAL),
    )  # fmt: skip
    links = (
        call("web", "api"),
        call("api", "bus", kind=ConnectionKind.PUBLISH, protocol="kafka", interaction=None),
        call("worker", "bus", kind=ConnectionKind.CONSUME, protocol="kafka", interaction=None),
        call("api", "cache", kind=ConnectionKind.DATA_ACCESS, protocol="redis", critical=False),
        call("api", "audit", interaction=Interaction.ASYNCHRONOUS),
        data("api", "db"),
        call("db", "replica", kind=ConnectionKind.REPLICATION, protocol="postgresql", interaction=None),
        call("api", "vault", kind=ConnectionKind.DEPENDENCY, protocol=None, interaction=None),
    )
    [path] = run(nodes, links).paths
    assert path.node_ids == ("web", "api", "db", "vault")  # breadth first, connection ids in order
    assert path.connection_ids == ("api-db", "api-vault", "web-api")
    assert path.optional_connection_ids == ("api-audit", "api-bus", "api-cache")
    assert "worker" not in path.node_ids and "replica" not in path.node_ids  # noqa: PT018


def test_cycles_are_safe_and_reported() -> None:
    nodes = (WEB, comp("api", **SOLID), comp("orders", **SOLID))
    result = run(nodes, (call("web", "api"), call("api", "orders"), call("orders", "api")))
    [path] = result.paths
    assert path.node_ids == ("web", "api", "orders")
    [cycle] = findings(result, T.CIRCULAR_DEPENDENCY)
    assert cycle.node_ids == ("api", "orders")


def test_disconnected_components_are_on_no_path() -> None:
    result = run((WEB, comp("api", **SOLID), comp("batch", replicas=1)), (call("web", "api"),))
    assert all("batch" not in p.node_ids for p in result.paths)
    [lonely] = findings(result, T.NO_REDUNDANCY)
    assert (lonely.node_ids, lonely.severity) == (("batch",), Severity.LOW)
    assert findings(result, T.SINGLE_POINT_OF_FAILURE) == []


def test_entries_and_their_absence() -> None:
    result = run((comp("api", replicas=1), comp("db", NodeKind.DATABASE, replicas=1)),
                 (data("api", "db"),), entries=("api",))  # fmt: skip
    [path] = result.paths
    assert path.node_ids == ("api", "db")
    assert {f.node_ids for f in findings(result, T.SINGLE_POINT_OF_FAILURE)} == {("api",), ("db",)}
    none = run((comp("api"),))
    assert [(u.element_id, u.code) for u in none.unsupported] == [("architecture", "no_entry")]
    with pytest.raises(InvalidReliabilityRequest) as error:
        run((WEB, comp("api")), entries=("ghost",))
    assert error.value.details == {"field": "entries", "reason": "unknown_node", "node_id": "ghost"}
    with pytest.raises(InvalidReliabilityRequest):
        run(
            (WEB, comp("api")),
            objectives=(Objective("n", ObjectiveKind.REDUNDANCY, target=Decimal(2), node_ids=("web",)),),
        )


# --- single points of failure and redundancy -----------------------------------------------------


def test_how_sure_a_single_point_of_failure_is() -> None:
    nodes = (WEB, node("mobile", NodeKind.CLIENT), comp("api", replicas=2, min_healthy_replicas=2),
             comp("db", NodeKind.DATABASE), comp("admin", replicas=1))  # fmt: skip
    links = (call("web", "api"), call("mobile", "api"), data("api", "db"), call("web", "admin"))
    spofs = {
        f.node_ids[0]: (f.severity, f.certainty, f.missing)
        for f in findings(run(nodes, links), T.SINGLE_POINT_OF_FAILURE)
    }
    assert spofs == {
        "api": (Severity.HIGH, Certainty.MODELED, ()),  # every replica is required
        "db": (Severity.HIGH, Certainty.CANDIDATE, ("configuration.replicas",)),  # redundancy not declared
        "admin": (Severity.MEDIUM, Certainty.MODELED, ()),  # only the web path needs it
    }


def test_declared_redundancy_is_not_a_single_point_but_is_questioned() -> None:
    result = run((WEB, comp("api", replicas=3, min_healthy_replicas=2)), (call("web", "api"),))
    assert findings(result, T.SINGLE_POINT_OF_FAILURE) == []
    kinds = {f.type: f.certainty for f in result.findings if f.node_ids == ("api",)}
    assert kinds == {
        T.REDUNDANCY_WITHOUT_FAILURE_DOMAIN_SEPARATION: Certainty.CANDIDATE,
        T.POTENTIAL_CORRELATED_FAILURE: Certainty.CANDIDATE,
        T.MISSING_FAILOVER: Certainty.CANDIDATE,
        T.MISSING_RECOVERY_DATA: Certainty.CANDIDATE,
    }
    solid = run((WEB, comp("api", **SOLID)), (call("web", "api"),))
    assert solid.findings == ()


def test_declared_facts_make_a_finding_modeled() -> None:
    api = comp(
        "api",
        **(
            SOLID
            | {"availability_zones": ("a",), "failure_independence": "correlated", "failover_mode": "none"}
        ),
    )
    kinds = {f.type: (f.severity, f.certainty) for f in run((WEB, api), (call("web", "api"),)).findings}
    assert kinds == {
        T.REDUNDANCY_WITHOUT_FAILURE_DOMAIN_SEPARATION: (Severity.MEDIUM, Certainty.MODELED),
        T.POTENTIAL_CORRELATED_FAILURE: (Severity.MEDIUM, Certainty.MODELED),
        T.MISSING_FAILOVER: (Severity.MEDIUM, Certainty.MODELED),
    }


def test_redundancy_groups() -> None:
    region: dict[str, Any] = {
        "failure_independence": "independent",
        "failover_mode": "automatic",
        "failover_seconds": 60,
        "mttr_seconds": 900,
    }
    nodes = (WEB, comp("lb", NodeKind.LOAD_BALANCER, **SOLID),
             comp("eu", redundancy_group="api", redundancy_group_min_healthy=1, region="eu-west-1", **region),
             comp("us", redundancy_group="api", redundancy_group_min_healthy=2, region="us-east-1", **region),
             comp("lonely", redundancy_group="solo", **SOLID))  # fmt: skip
    result = run(nodes, (call("web", "lb"), call("lb", "eu")))
    groups = {(f.title, f.node_ids) for f in findings(result, T.INCONSISTENT_REDUNDANCY)}
    assert groups == {
        ("The redundancy group api's members disagree on its minimum", ("eu", "us")),
        ("Part of the redundancy group api is not on any request path", ("us",)),
        ("The redundancy group solo has one member", ("lonely",)),
    }
    assert "eu" not in {
        f.node_ids[0] for f in findings(result, T.SINGLE_POINT_OF_FAILURE)
    }  # it has an alternative
    assert findings(result, T.REDUNDANCY_WITHOUT_FAILURE_DOMAIN_SEPARATION) == []  # different regions


def test_a_critical_dependency_on_a_single_point() -> None:
    nodes = (WEB, comp("api", **SOLID), comp("payments", NodeKind.EXTERNAL))
    links = (call("web", "api"), call("api", "payments", critical=True))
    result = run(nodes, links)
    [critical] = findings(result, T.CRITICAL_DEPENDENCY_WITHOUT_ALTERNATIVE)
    assert (critical.node_ids, critical.connection_ids, critical.certainty) == (
        ("api", "payments"),
        ("api-payments",),
        Certainty.CANDIDATE,
    )


def test_an_unstated_interaction_is_treated_as_waiting_and_reported() -> None:
    result = run((WEB, comp("api", **SOLID), comp("db", NodeKind.DATABASE, **SOLID)),
                 (call("web", "api"), data("api", "db", interaction=None)))  # fmt: skip
    assert result.paths[0].node_ids == ("web", "api", "db")
    [unmodeled] = findings(result, T.UNMODELED_DEPENDENCY)
    assert unmodeled.missing == ("api-db.interaction",)


# --- determinism ---------------------------------------------------------------------------------


def test_analysis_is_deterministic_and_leaves_the_architecture_unchanged() -> None:
    nodes = (WEB, comp("api", replicas=1), comp("db", NodeKind.DATABASE))
    links = (call("web", "api"), data("api", "db"))
    ir = ArchitectureIR("Shop", nodes=nodes, connections=links)
    before = repr(ir)
    context = ReliabilityContext(ir, REVISION, ReliabilityAnalysisRequest(uuid.UUID(int=1), 1))
    first = analyze(context, Registry([Declared()], [RequestPaths(), TopologyFindings()]))
    second = run(tuple(reversed(nodes)), tuple(reversed(links)))
    assert first.to_dict() == second.to_dict()
    assert [f.id for f in first.findings] == [f.id for f in second.findings]
    assert repr(ir) == before
