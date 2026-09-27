"""Failure and resilience scenarios (Milestone 12, phase 6), judged with the Reliability Engine's
semantics: required and optional dependencies, declared redundancy groups and failover, no invented
probabilities, independence or failover, unknown recovery behavior kept unknown, and resilience
changes evaluated by the engine itself."""

import json
import uuid
from decimal import Decimal
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.simulations.entities import SimulationRequest
from core.domain.simulations.results import EntryImpact, SimulationResult
from core.domain.simulations.scenarios import ConfigurationChange, Failure, Scenario
from core.domain.simulations.values import AnalysisKind, FailureKind, Impact, RunState
from core.domain.validation.options import RevisionInfo
from engines.reliability.service import DeterministicReliabilityEngine
from engines.simulation.context import SimulationContext
from engines.simulation.engine import analyze
from engines.simulation.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node

A = AnalysisKind
K = FailureKind
D = Decimal
COMMON: dict[str, ConfigValue] = {"availability": D("0.999"), "failure_independence": "independent"}
GROUP: dict[str, Any] = {
    "redundancy_group": "api",
    "redundancy_group_min_healthy": 1,
    "failover_mode": "automatic",
}
PROTOCOLS = {ConnectionKind.REQUEST: "https", ConnectionKind.DATA_ACCESS: "postgresql"}


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    config: dict[str, ConfigValue] = {k: v for k, v in {**COMMON, **values}.items() if v is not None}
    return node(node_id, kind, configuration=Configuration(config))


def api(node_id: str, zone: str, **values: Any) -> Node:
    return component(node_id, availability_zones=(zone,), **(GROUP | values))


def link(source: str, target: str, kind: ConnectionKind = ConnectionKind.REQUEST) -> Connection:
    waits = kind in PROTOCOLS
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=kind,
        protocol=PROTOCOLS.get(kind, "amqp"),
        interaction=Interaction.SYNCHRONOUS if waits else None,
    )


def shop(*extra: Node, extra_links: tuple[Connection, ...] = (), **apis: dict[str, Any]) -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            api("api-a", "eu-west-1a", **apis.get("a", {})),
            api("api-b", "eu-west-1b", **apis.get("b", {})),
            component("db", NodeKind.DATABASE, availability=D("0.99"), availability_zones=("eu-west-1a",)),
            component("queue", NodeKind.QUEUE, availability_zones=("eu-west-1a", "eu-west-1b")),
            *extra,
        ),
        connections=(
            link("web", "api-a"),
            link("web", "api-b"),
            link("api-a", "db", ConnectionKind.DATA_ACCESS),
            link("api-b", "db", ConnectionKind.DATA_ACCESS),
            link("api-a", "queue", ConnectionKind.PUBLISH),
            *extra_links,
        ),
    )


def simulate(scenario: Scenario, ir: ArchitectureIR | None = None) -> SimulationResult:
    ir = ir or shop()
    request = SimulationRequest(uuid.UUID(int=1), 1, scenario, analyses=(A.RELIABILITY,))
    context = SimulationContext(ir, RevisionInfo("arch-1", 1, content_hash(ir)), request)
    return analyze(context, default_registry())


def web(result: SimulationResult) -> EntryImpact:
    [entry] = [e for e in result.entries if e.entry_id == "web"]
    return entry


def down(*targets: str, kind: FailureKind = K.COMPONENT) -> Scenario:
    return Scenario("Outage", failures=tuple(Failure(kind, t) for t in targets))


def test_fixture_4_a_component_failure_with_explicit_dependency_semantics() -> None:
    result = simulate(down("db"))
    entry = web(result)
    assert (entry.impact, entry.through, entry.missing) == (Impact.INTERRUPTED, ("db",), ())
    impacts = {c.node_id: (c.unavailable, c.impact) for c in result.components}
    assert impacts == {
        "api-a": (False, Impact.INTERRUPTED),  # requires db
        "api-b": (False, Impact.INTERRUPTED),
        "db": (True, None),
    }


def test_declared_automatic_alternatives_tolerate_a_failure() -> None:
    entry = web(simulate(down("api-a")))
    assert (entry.impact, entry.through) == (Impact.TOLERATED, ("api-a",))
    assert "covered" in entry.explanation


def test_fixture_5_a_failure_with_unknown_failover_behavior() -> None:
    ir = shop(a={"failover_mode": None}, b={"failover_mode": None})
    result = simulate(down("api-a"), ir)
    entry = web(result)
    assert entry.impact is Impact.UNKNOWN  # automatic failover is never assumed
    assert entry.missing == ("api-a.configuration.failover_mode", "api-b.configuration.failover_mode")
    [run] = result.runs
    assert run.state is RunState.PARTIAL  # unknown recovery behavior stays unknown


def test_manual_failover_or_too_few_alternatives_interrupt() -> None:
    manual = shop(a={"failover_mode": "manual"})
    assert web(simulate(down("api-a"), manual)).impact is Impact.INTERRUPTED
    two_needed = shop(a={"redundancy_group_min_healthy": 2}, b={"redundancy_group_min_healthy": 2})
    assert web(simulate(down("api-a"), two_needed)).impact is Impact.INTERRUPTED
    undeclared = shop(a={"redundancy_group_min_healthy": None})
    entry = web(simulate(down("api-a"), undeclared))
    missing = ("api-a.configuration.redundancy_group_min_healthy",)
    assert (entry.impact, entry.missing) == (Impact.UNKNOWN, missing)


def test_losing_an_optional_dependency_degrades() -> None:
    result = simulate(down("queue"))
    entry = web(result)
    assert (entry.impact, entry.through) == (Impact.DEGRADED, ("api-a-queue",))
    assert {c.node_id: c.impact for c in result.components}["api-a"] is Impact.DEGRADED


def test_a_connection_failure_follows_the_routes_that_remain() -> None:
    entry = web(simulate(down("web-api-a", kind=K.CONNECTION)))
    assert (entry.impact, entry.through) == (Impact.TOLERATED, ("web-api-a",))  # api-b still reachable
    entry = web(simulate(down("api-a-db", "api-b-db", kind=K.CONNECTION)))
    assert entry.impact is Impact.INTERRUPTED  # no route to db, and no alternative to db


def test_a_zone_failure_uses_declared_placement_and_unknown_stays_unknown() -> None:
    entry = web(simulate(down("eu-west-1a", kind=K.ZONE)))
    assert entry.impact is Impact.INTERRUPTED  # db is declared only in that zone
    assert set(entry.through) == {"api-a", "db"}  # api-a's loss is covered, db's is not
    cache = node("cache", NodeKind.CACHE)  # declares nothing, no zone
    undeclared = shop(cache, extra_links=(link("api-b", "cache"),))
    entry = web(simulate(down("eu-west-1b", kind=K.ZONE), undeclared))
    assert entry.impact is Impact.UNKNOWN
    assert "cache.configuration.availability_zones" in entry.missing


def test_resilience_changes_are_evaluated_by_the_reliability_engine() -> None:
    better = Scenario("Better db", changes=(ConfigurationChange("db", "availability", D("0.9999")),))
    result = simulate(better)
    [path] = result.deltas
    assert (path.element_id, path.metric, path.unit) == ("web", "path.availability", "ratio")
    assert (path.baseline, path.scenario) == (D("0.98999901"), D("0.999899"))
    assert result.entries == ()  # no failure, nothing judged
    [run] = result.runs
    ir = shop()
    request = ReliabilityAnalysisRequest(uuid.UUID(int=1), 1)
    baseline = DeterministicReliabilityEngine().analyze(
        ir, RevisionInfo("arch-1", 1, content_hash(ir)), request, ()
    )
    assert (run.model_set, run.baseline_fingerprint) == (baseline.model_set, baseline.fingerprint)


def test_nothing_is_modified_and_the_result_is_deterministic() -> None:
    ir = shop()
    before = json.dumps(to_dict(ir), sort_keys=True)
    first, again = simulate(down("db"), ir), simulate(down("db"), ir)
    assert first.to_dict() == again.to_dict()
    assert json.dumps(to_dict(ir), sort_keys=True) == before
    outcomes = {k: v for k, v in first.to_dict().items() if k in {"entries", "components", "deltas"}}
    serialized = json.dumps(outcomes).lower()  # the outcomes, not the engine's model names
    for invented in ("probability", "mttr", "downtime", "outage_minutes"):
        assert invented not in serialized
