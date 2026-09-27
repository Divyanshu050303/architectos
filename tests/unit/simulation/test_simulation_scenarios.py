"""Scenario types and their validation against the exact revision (Milestone 12, phase 2): every
supported type declares its inputs, units, elements, overlay, analyses, unsupported conditions and
limits; a scenario is checked, deterministically and without changing anything, before it is run."""

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import to_dict
from core.domain.simulations.catalog import (
    BY_ID,
    CAPACITY_PROPERTIES,
    COST_PROPERTIES,
    RELIABILITY_PROPERTIES,
    TYPES,
    analyses_of_change,
    type_of_change,
)
from core.domain.simulations.entities import SimulationRequest
from core.domain.simulations.errors import InvalidSimulationRequest
from core.domain.simulations.scenarios import ConfigurationChange, Failure, Scenario, WorkloadChange
from core.domain.simulations.values import AnalysisKind, FailureKind
from engines.cost import mapping
from engines.simulation.validation import check_scenario
from tests.unit.architecture_ir.builders import connection, node

A = AnalysisKind
K = FailureKind


def shop() -> ArchitectureIR:
    api: dict[str, ConfigValue] = {
        "replicas": 2,
        "region": "eu-west-1",
        "availability_zones": ("eu-west-1a", "eu-west-1b"),
    }
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("zone", NodeKind.BOUNDARY),
            node("api", configuration=Configuration(api)),
            node(
                "db", NodeKind.DATABASE, configuration=Configuration({"availability_zones": ("eu-west-1a",)})
            ),
            node("queue", NodeKind.QUEUE),
        ),
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https"),
            connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql"),
        ),
    )


def request(scenario: Scenario, **fields: Any) -> SimulationRequest:
    return SimulationRequest(uuid.UUID(int=1), 1, scenario, **fields)


def refused(scenario: Scenario, **fields: Any) -> tuple[str, str, str]:
    with pytest.raises(InvalidSimulationRequest) as raised:
        check_scenario(shop(), request(scenario, **fields))
    details = raised.value.details
    return details["field"], details["reason"], details["element_id"]


def change(element: str, name: str, value: Any) -> Scenario:
    return Scenario("S", changes=(ConfigurationChange(element, name, value),))


def failure(kind: FailureKind, target: str) -> Scenario:
    return Scenario("S", failures=(Failure(kind, target),))


# --- the catalog ---------------------------------------------------------------------------------


def test_every_type_declares_its_contract() -> None:
    assert [t.id for t in TYPES] == [
        "workload", "replicas", "traffic", "resilience", "resources",
        "component_failure", "connection_failure", "zone_failure", "region_failure",
    ]  # fmt: skip
    for scenario_type in TYPES:
        declared = scenario_type.to_dict()
        assert declared["version"] >= 1
        for field in ("name", "description", "applies_to", "overlay", "output"):
            assert declared[field], (scenario_type.id, field)
        assert declared["inputs"], scenario_type.id
        assert declared["analyses"], scenario_type.id
        assert declared["unsupported"], scenario_type.id
        assert declared["limit"] >= 1
    assert json.dumps([t.to_dict() for t in TYPES])  # serializable, as the catalog endpoint returns it


def test_configuration_types_partition_the_simulated_properties() -> None:
    configuration = [t for t in TYPES if t.properties]
    for i, first in enumerate(configuration):
        for second in configuration[i + 1 :]:
            assert not first.properties & second.properties, (first.id, second.id)
    simulated = CAPACITY_PROPERTIES | RELIABILITY_PROPERTIES | COST_PROPERTIES
    assert set().union(*(t.properties for t in configuration)) == simulated


def test_the_cost_properties_are_what_the_cost_engine_maps() -> None:
    assert {mapping.SERVICE, mapping.MAIN_SKU, mapping.STORAGE_SKU, mapping.CONDITIONS} <= COST_PROPERTIES


def test_a_change_concerns_the_engines_that_read_its_property() -> None:
    every = (A.CAPACITY, A.RELIABILITY, A.COST)
    assert analyses_of_change(ConfigurationChange("api", "replicas", 3)) == every
    assert analyses_of_change(ConfigurationChange("api", "cpu_limit_cores", Decimal(2))) == (A.CAPACITY,)
    assert analyses_of_change(ConfigurationChange("api", "failover_mode", "automatic")) == (A.RELIABILITY,)
    assert analyses_of_change(ConfigurationChange("api", "pricing_sku", "m6g.large")) == (A.COST,)
    assert type_of_change(ConfigurationChange("api", "exposure", "public")) is None  # no simulated engine
    assert BY_ID["workload"].analyses == (A.CAPACITY, A.COST)
    assert BY_ID["zone_failure"].analyses == (A.RELIABILITY,)


# --- validation against the revision -------------------------------------------------------------


def test_a_valid_scenario_is_typed_part_by_part() -> None:
    scenario = Scenario(
        "Launch",
        workload=WorkloadChange(growth=Decimal(2)),
        changes=(ConfigurationChange("api", "replicas", 4), ConfigurationChange("api-db", "pool_size", 20)),
        failures=(Failure(K.ZONE, "eu-west-1a"), Failure(K.COMPONENT, "queue")),
    )
    plan = check_scenario(shop(), request(scenario, entries=("web",)))
    assert [(p.key, p.type.id) for p in plan.parts] == [
        ("workload", "workload"),
        ("change:api.replicas", "replicas"),
        ("change:api-db.pool_size", "traffic"),
        ("failure:component:queue", "component_failure"),
        ("failure:zone:eu-west-1a", "zone_failure"),
    ]
    assert plan.analyses == (A.CAPACITY, A.RELIABILITY, A.COST)
    assert plan.types.models == (
        ("component_failure", 1),
        ("replicas", 1),
        ("traffic", 1),
        ("workload", 1),
        ("zone_failure", 1),
    )
    assert check_scenario(shop(), request(scenario, entries=("web",))) == plan  # deterministic


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        (change("ghost", "replicas", 2), ("scenario.changes.element_id", "unknown_element", "ghost")),
        (change("api", "partitions", 12), ("scenario.changes.property", "not_applicable", "api.partitions")),
        (change("api-db", "replicas", 2), ("scenario.changes.property", "not_applicable", "api-db.replicas")),
        (change("api", "exposure", "public"), ("scenario.changes.property", "not_simulated", "api.exposure")),
        (failure(K.COMPONENT, "ghost"), ("scenario.failures.target", "unknown_element", "ghost")),
        (failure(K.COMPONENT, "web"), ("scenario.failures.target", "not_a_component", "web")),
        (failure(K.COMPONENT, "zone"), ("scenario.failures.target", "not_a_component", "zone")),
        (failure(K.CONNECTION, "db-api"), ("scenario.failures.target", "unknown_element", "db-api")),
        (failure(K.ZONE, "us-east-1a"), ("scenario.failures.target", "unknown_zone", "us-east-1a")),
        (failure(K.REGION, "us-east-1"), ("scenario.failures.target", "unknown_region", "us-east-1")),
    ],
)
def test_invalid_scenarios_are_refused_with_the_field_and_element(
    scenario: Scenario, expected: tuple[str, str, str]
) -> None:
    assert refused(scenario) == expected


def test_entries_must_exist() -> None:
    scenario = Scenario("S", workload=WorkloadChange(growth=Decimal(2)))
    assert refused(scenario, entries=("mobile",)) == ("entries", "unknown_element", "mobile")


def test_validation_changes_nothing() -> None:
    ir = shop()
    before = json.dumps(to_dict(ir), sort_keys=True)
    scenario = Scenario(
        "S",
        changes=(ConfigurationChange("api", "replicas", 9),),
        failures=(Failure(K.REGION, "eu-west-1"),),
    )
    check_scenario(ir, request(scenario))
    assert json.dumps(to_dict(ir), sort_keys=True) == before
