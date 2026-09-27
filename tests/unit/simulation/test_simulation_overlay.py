"""The immutable scenario overlay (Milestone 12, phase 3): a scenario applied to an in-memory copy of
the exact revision with the IR's own edit commands, failures resolved from declared placement
without removing anything, deterministic, serializable and reconstructible — and the source
architecture never modified."""

import json
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.commands import (
    InvalidArchitectureCommand,
    UpdateConnectionConfiguration,
    apply_commands,
)
from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.provenance import ProvenanceSource
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.simulations.errors import InvalidSimulationRequest
from core.domain.simulations.overlay import apply_scenario, provenance_of, reconstruct
from core.domain.simulations.scenarios import ConfigurationChange, Failure, Scenario, WorkloadChange
from core.domain.simulations.values import FailureKind
from core.domain.validation.options import RevisionInfo
from tests.unit.architecture_ir.builders import connection, node

K = FailureKind


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    config: dict[str, ConfigValue] = values
    return node(node_id, kind, configuration=Configuration(config))


def shop() -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            component(
                "api",
                replicas=2,
                autoscaling_min_replicas=2,
                autoscaling_max_replicas=4,
                region="eu-west-1",
                availability_zones=("eu-west-1a", "eu-west-1b"),
            ),
            component("db", NodeKind.DATABASE, region="eu-west-1", availability_zones=("eu-west-1a",)),
            component("queue", NodeKind.QUEUE),
        ),
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https"),
            connection(
                "api-db",
                "api",
                "db",
                kind=ConnectionKind.DATA_ACCESS,
                protocol="postgresql",
                configuration=Configuration({"pool_size": 10}),
            ),
        ),
    )


def revision(ir: ArchitectureIR) -> RevisionInfo:
    return RevisionInfo("arch-1", 3, content_hash(ir))


def frozen(ir: ArchitectureIR) -> str:
    return json.dumps(to_dict(ir), sort_keys=True)


RESIZE = Scenario(
    "Resize",
    changes=(
        ConfigurationChange("api", "replicas", 4),
        ConfigurationChange("api", "cpu_limit_cores", Decimal("1.5")),
        ConfigurationChange("api-db", "pool_size", 40),
    ),
)


def test_changes_apply_to_a_copy_and_the_revision_is_never_modified() -> None:
    ir = shop()
    before = frozen(ir)
    overlay = apply_scenario(ir, revision(ir), RESIZE)
    assert frozen(ir) == before  # the source architecture is untouched
    assert overlay.baseline is ir
    assert overlay.architecture is not ir
    api = overlay.architecture.node("api")
    assert api is not None
    assert (api.configuration.get("replicas"), api.configuration.get("cpu_limit_cores")) == (
        4,
        Decimal("1.5"),
    )
    pool = overlay.architecture.connection("api-db")
    assert pool is not None
    assert pool.configuration.get("pool_size") == 40
    assert [n.id for n in overlay.architecture.nodes] == [n.id for n in ir.nodes]  # ids preserved
    assert [c.id for c in overlay.architecture.connections] == [c.id for c in ir.connections]
    assert overlay.scenario_hash != revision(ir).content_hash  # baseline and scenario told apart


def test_changed_values_are_explicit_and_carry_the_scenarios_provenance() -> None:
    ir = shop()
    overlay = apply_scenario(ir, revision(ir), RESIZE)
    assert [c.evidence().to_dict() for c in overlay.changes] == [
        {"label": "api.cpu_limit_cores", "value": "not declared -> 1.5"},
        {"label": "api.replicas", "value": "2 -> 4"},
        {"label": "api-db.pool_size", "value": "10 -> 40"},
    ]
    api = overlay.architecture.node("api")
    assert api is not None
    stamped = api.field_provenance["configuration.replicas"]
    assert stamped == provenance_of(RESIZE)
    assert (stamped.source, stamped.actor, stamped.verified) == (
        ProvenanceSource.USER_INPUT,
        "simulation",
        False,
    )
    assert stamped.reference == f"scenario:{RESIZE.fingerprint[:16]}"
    assert "configuration.region" not in api.field_provenance  # only what changed is attributed


def test_a_change_that_breaks_an_invariant_is_refused() -> None:
    ir = shop()
    below_minimum = Scenario("S", changes=(ConfigurationChange("api", "autoscaling_max_replicas", 1),))
    with pytest.raises(InvalidSimulationRequest) as raised:
        apply_scenario(ir, revision(ir), below_minimum)
    assert raised.value.details["reason"] == "inconsistent"
    assert raised.value.details["field"] == "scenario.changes"


def test_failures_mark_elements_unavailable_without_removing_them() -> None:
    ir = shop()
    scenario = Scenario("Outage", failures=(Failure(K.COMPONENT, "queue"), Failure(K.CONNECTION, "api-db")))
    overlay = apply_scenario(ir, revision(ir), scenario)
    assert (overlay.unavailable_nodes, overlay.unavailable_connections) == (("queue",), ("api-db",))
    assert overlay.architecture is ir  # nothing configured, nothing removed
    assert overlay.scenario_hash == revision(ir).content_hash


def test_a_zone_failure_follows_declared_zones_and_unknown_stays_unknown() -> None:
    ir = shop()
    overlay = apply_scenario(ir, revision(ir), Scenario("Zone", failures=(Failure(K.ZONE, "eu-west-1a"),)))
    assert overlay.unavailable_nodes == ("db",)  # declares only that zone
    assert overlay.zone_losses == (("api", "eu-west-1a"),)  # keeps running in eu-west-1b
    assert overlay.undetermined_nodes == ("queue",)  # declares no zone: unknown, never assumed
    assert "web" not in overlay.undetermined_nodes  # clients never fail


def test_a_region_failure_follows_declared_regions() -> None:
    ir = shop()
    both = Scenario("Region", failures=(Failure(K.REGION, "eu-west-1"), Failure(K.COMPONENT, "queue")))
    overlay = apply_scenario(ir, revision(ir), both)
    assert overlay.unavailable_nodes == ("api", "db", "queue")
    assert overlay.undetermined_nodes == ()  # queue is unavailable by name, not undetermined


def test_the_overlay_is_deterministic_serializable_and_reconstructible() -> None:
    ir = shop()
    scenario = Scenario(
        "Everything",
        workload=WorkloadChange(growth=Decimal(2)),
        changes=RESIZE.changes,
        failures=(Failure(K.ZONE, "eu-west-1a"),),
    )
    first = apply_scenario(ir, revision(ir), scenario)
    again = apply_scenario(shop(), revision(shop()), Scenario.from_dict(scenario.to_dict()))
    assert first.to_dict() == again.to_dict()
    assert first.trace() == again.trace()
    stored = {"scenario": scenario.to_dict(), "overlay": json.loads(json.dumps(first.to_dict()))}
    rebuilt = reconstruct(shop(), revision(shop()), stored)
    assert rebuilt.to_dict() == first.to_dict()
    assert '"nodes"' not in json.dumps(first.to_dict())  # the architecture itself is never stored
    edited = apply_commands(shop(), [UpdateConnectionConfiguration("api-db", {"pool_size": 12})])
    with pytest.raises(InvalidSimulationRequest) as raised:
        reconstruct(edited, revision(edited), stored)
    assert raised.value.details == {"field": "overlay", "reason": "not_reproducible"}


def test_the_trace_explains_each_step() -> None:
    ir = shop()
    scenario = Scenario("S", changes=RESIZE.changes[1:2], failures=(Failure(K.ZONE, "eu-west-1a"),))
    labels = [e.label for e in apply_scenario(ir, revision(ir), scenario).trace()]
    assert labels == [
        "overlay.revision",
        "overlay.change.api.replicas",
        "overlay.unavailable.db",
        "overlay.zone_loss.api",
        "overlay.undetermined.queue",
        "overlay.scenario_content_hash",
    ]


def test_the_connection_configuration_command() -> None:
    ir = shop()
    changed = apply_commands(ir, [UpdateConnectionConfiguration("api-db", {"pool_size": None})])
    pool = changed.connection("api-db")
    assert pool is not None
    assert pool.configuration.get("pool_size") is None  # cleared: not declared, not 0
    with pytest.raises(InvalidArchitectureCommand) as raised:
        apply_commands(ir, [UpdateConnectionConfiguration("ghost", {"pool_size": 1})])
    assert raised.value.details["reason"] == "unknown_connection"
