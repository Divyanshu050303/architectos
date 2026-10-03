"""Engine impact (spec 12, 24.3): the real engines analyze both states with the same inputs; findings
compared by stable id; capacity and cost only on the inputs a person named, never estimated."""

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain.architecture_diff.impacts import EngineImpact
from core.domain.architecture_diff.ports import CapacityInputs, CostInputs, ImpactInputs
from core.domain.architecture_diff.values import FindingState, ImpactStatus
from engines.architecture_diff.impact import (
    IDENTICAL,
    NO_PRICING,
    NO_WORKLOAD,
    DiffEngines,
    ImpactComparer,
    State,
)
from engines.capacity.service import DeterministicCapacityEngine
from engines.cost.service import DeterministicCostEngine
from engines.observability.service import DeterministicObservabilityEngine
from engines.reliability.service import DeterministicReliabilityEngine
from engines.security.service import DeterministicSecurityEngine
from engines.validation.service import DeterministicValidationEngine
from persistence.component_catalog import default_catalog
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.cost.test_pricing import snapshot
from tests.unit.simulation.test_simulation_engine import workload

ARCH = uuid.uuid4()
CAPACITY = DeterministicCapacityEngine()
ENGINES = DiffEngines(
    DeterministicValidationEngine(catalog=default_catalog()),
    DeterministicReliabilityEngine(),
    DeterministicSecurityEngine(),
    DeterministicObservabilityEngine(),
    CAPACITY,
    DeterministicCostEngine(capacity=CAPACITY),
)
PRICED = {"pricing_service": "rds", "pricing_sku": "db.r6g.large", "region": "eu-west-1"}


def shop(db: dict[str, Any] | None = None, api: dict[str, Any] | None = None) -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("api", configuration=Configuration({"replicas": 2} | (api or {}))),
            node("db", NodeKind.DATABASE, configuration=Configuration(PRICED | (db or {}))),
        ),
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https"),
            connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql"),
        ),
    )


def state(ir: ArchitectureIR, number: int) -> State:
    return State(ir, ARCH, number, content_hash(ir))


def compare(
    base: ArchitectureIR,
    target: ArchitectureIR,
    inputs: ImpactInputs | None = None,
    engines: DiffEngines = ENGINES,
) -> dict[str, EngineImpact]:
    impacts = ImpactComparer(engines).compare(state(base, 1), state(target, 2), inputs or ImpactInputs())
    return {i.engine: i for i in impacts.engines}


def cost_inputs() -> CostInputs:
    return CostInputs(uuid.uuid4(), snapshot(), "USD", date(2026, 9, 15), Decimal(730), "aws")


# --- the four engines that need nothing more ----------------------------------------------------


def test_identical_states_are_not_analyzed() -> None:
    found = compare(shop(), shop())
    assert set(found) == {"validation", "reliability", "security", "observability", "capacity", "cost"}
    for impact in found.values():
        assert impact.status is ImpactStatus.NOT_EVALUATED
        assert impact.limitations == (IDENTICAL,)


def test_a_security_change_shows_up_as_the_engine_says() -> None:
    security = compare(shop(), shop(db={"exposure": "public"}))["security"]
    assert security.status is ImpactStatus.EVALUATED
    assert security.of_state(FindingState.INTRODUCED), "the engine reports something about a public database"
    assert security.target_summary != security.base_summary  # each state's own counts


def test_findings_are_compared_by_stable_id() -> None:
    exposed = shop(db={"exposure": "public"})
    forward, back = compare(shop(), exposed)["security"], compare(exposed, shop())["security"]
    introduced = {f.finding_id for f in forward.of_state(FindingState.INTRODUCED)}
    assert introduced == {f.finding_id for f in back.of_state(FindingState.RESOLVED)}
    assert forward.unchanged == back.unchanged


def test_every_analysis_engine_reports() -> None:
    found = compare(shop(), shop(api={"replicas": 3}))
    for engine in ("validation", "reliability", "security", "observability"):
        assert found[engine].status is ImpactStatus.EVALUATED, engine
        assert found[engine].versions, engine


def test_no_requirements_means_no_verdicts_never_invented() -> None:
    comparer = ImpactComparer(ENGINES)
    impacts = comparer.compare(state(shop(), 1), state(shop(api={"replicas": 3}), 2), ImpactInputs())
    assert impacts.verdicts == ({}, {})


# --- capacity and cost: only on named inputs --------------------------------------------------


def test_without_named_inputs_capacity_and_cost_are_not_evaluated() -> None:
    found = compare(shop(), shop(api={"replicas": 3}))
    assert found["capacity"].limitations == (NO_WORKLOAD,)
    assert found["cost"].limitations == (NO_PRICING,)
    assert found["capacity"].measures == found["cost"].measures == ()


def test_a_replica_change_reaches_the_capacity_engine() -> None:
    inputs = ImpactInputs(capacity=CapacityInputs(uuid.uuid4(), workload()))
    capacity = compare(shop(api={"replicas": 1}), shop(api={"replicas": 4}), inputs)["capacity"]
    assert capacity.status is ImpactStatus.EVALUATED
    assert capacity.base_summary
    assert capacity.target_summary
    assert any("workload of capacity analysis" in limit for limit in capacity.limitations)
    for measure in capacity.measures:  # only what the engine calculated in both states
        assert measure.before
        assert measure.after


def test_cost_is_priced_on_one_snapshot_for_both_states() -> None:
    base, target = shop(db={"replicas": 1}), shop(db={"replicas": 2})  # quantities the engine can bill
    cost = compare(base, target, ImpactInputs(cost=cost_inputs()))["cost"]
    assert cost.status is ImpactStatus.EVALUATED, cost.limitations
    assert cost.versions["snapshot_hash"] == snapshot().content_hash
    assert any("declared resources" in limit for limit in cost.limitations)
    [total] = cost.measures
    assert (total.name, total.unit) == ("known_monthly_total", "USD")
    assert Decimal(total.after) > Decimal(total.before)  # as the cost engine priced it, not assumed


def test_a_state_that_cannot_be_priced_has_no_total() -> None:
    bare = shop()
    unpriced = replace(
        bare,
        nodes=tuple(replace(n, configuration=Configuration()) if n.id == "db" else n for n in bare.nodes),
    )
    cost = compare(unpriced, replace(unpriced, name="Shop 2"), ImpactInputs(cost=cost_inputs()))["cost"]
    assert cost.measures == ()  # a 0 from "nothing priced" is not a cost
    assert any("could not be priced" in limit for limit in cost.limitations)


# --- failures --------------------------------------------------------------------------------


class _Broken:
    def analyze(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("bug")


def test_a_failing_engine_is_reported_never_hidden() -> None:
    engines = replace(ENGINES, reliability=_Broken())  # type: ignore[arg-type]
    found = compare(shop(), shop(api={"replicas": 3}), engines=engines)
    assert found["reliability"].status is ImpactStatus.FAILED
    assert found["reliability"].error == "engine_error"
    assert found["reliability"].findings == ()
    assert found["security"].status is ImpactStatus.EVALUATED  # the others still compare


def test_the_comparison_is_deterministic() -> None:
    inputs = ImpactInputs(cost=cost_inputs(), capacity=CapacityInputs(uuid.UUID(int=9), workload()))
    target = shop(db={"exposure": "public", "replicas": 2})
    first, second = compare(shop(), target, inputs), compare(shop(), target, inputs)
    assert {k: v.to_dict() for k, v in first.items()} == {k: v.to_dict() for k, v in second.items()}
