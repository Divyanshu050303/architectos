"""Component reliability calculations (Milestone 9, phase 4): each formula, its inputs and trace,
and every reason it gives no value."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.results import ComponentStatus, Source
from core.domain.capacity.units import Quantity
from core.domain.engine_results import Evidence
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.reliability.results import ComponentResult, ReliabilityResult
from core.domain.validation.options import RevisionInfo
from engines.reliability.availability import k_of_n
from engines.reliability.context import ReliabilityContext
from engines.reliability.engine import analyze
from engines.reliability.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
REDUNDANT: dict[str, Any] = {
    "replicas": 3, "min_healthy_replicas": 2, "replica_availability": Decimal("0.99"),
    "failure_independence": "independent", "failover_mode": "automatic",
}  # fmt: skip


def run(*nodes: Any, links: tuple[Any, ...] = ()) -> ReliabilityResult:
    ir = ArchitectureIR("Shop", nodes=(node("web", NodeKind.CLIENT), *nodes), connections=links)
    context = ReliabilityContext(ir, REVISION, ReliabilityAnalysisRequest(uuid.UUID(int=1), 1))
    return analyze(context, default_registry())


def one(kind: NodeKind = NodeKind.SERVICE, **values: Any) -> ComponentResult:
    [component] = run(node("c", kind, configuration=Configuration(values))).components
    return component


def value(component: ComponentResult, resource: str) -> Decimal | None:
    estimate = component.estimate(resource)
    return estimate.quantity.value if estimate and estimate.quantity else None


# --- availability from MTBF and MTTR -------------------------------------------------------------


def test_availability_from_mtbf_and_mttr() -> None:
    component = one(mtbf_seconds=999, mttr_seconds=1)
    estimate = component.estimate("replica_availability")
    assert estimate is not None
    assert (estimate.quantity, estimate.source, estimate.model_id) == (
        Quantity.of("0.999", "ratio"),
        Source.MODEL_ESTIMATE,
        "mtbf-mttr",
    )
    assert estimate.basis == "mtbf_seconds / (mtbf_seconds + mttr_seconds)"
    assert estimate.inputs == (
        Evidence("configuration.mtbf_seconds", "999"),
        Evidence("configuration.mttr_seconds", "1"),
    )
    assert value(
        one(mtbf_seconds=Decimal("720000"), mttr_seconds=Decimal("3600")), "replica_availability"
    ) == Decimal("0.995024876")


def test_zero_and_undefined_denominators() -> None:
    assert (
        value(one(mtbf_seconds=0, mttr_seconds=5), "replica_availability") == 0
    )  # always failing, and so declared
    undefined = one(mtbf_seconds=0, mttr_seconds=0)
    estimate = undefined.estimate("replica_availability")
    assert estimate is not None
    assert (estimate.quantity, estimate.missing) == (None, ("defined.mtbf_seconds_plus_mttr_seconds",))
    assert value(undefined, "availability") is None  # no replica availability: nothing to combine
    assert value(undefined, "recovery_time") == 0  # a declared repair time of 0 s is known


def test_a_declared_value_takes_precedence_over_a_calculated_one() -> None:
    component = one(replica_availability=Decimal("0.98"), mtbf_seconds=999, mttr_seconds=1, replicas=1)
    assert value(component, "replica_availability") == Decimal("0.98")
    assert ("declared-replica-availability", 1) in component.models
    assert ("mtbf-mttr", 1) not in component.models  # not needed: the declared value was already known
    whole = one(availability=Decimal("0.9995"), replicas=1, replica_availability=Decimal("0.9"))
    estimate = whole.estimate("availability")
    assert estimate is not None
    assert (estimate.quantity, estimate.source) == (Quantity.of("0.9995", "ratio"), Source.DECLARED)


# --- replicated components -----------------------------------------------------------------------


def test_one_replica_is_the_component() -> None:
    assert value(one(replicas=1, mtbf_seconds=99, mttr_seconds=1), "availability") == Decimal("0.99")


def test_k_of_n_with_independent_failures_and_automatic_failover() -> None:
    assert k_of_n(Decimal("0.99"), 3, 2) == Decimal("0.999702")  # 3 x 0.99^2 x 0.01 + 0.99^3
    assert k_of_n(Decimal("0.9"), 2, 1) == Decimal("0.99")
    assert k_of_n(Decimal("0.9"), 2, 2) == Decimal("0.81")  # every replica needed: worse than one
    component = one(**REDUNDANT)
    estimate = component.estimate("availability")
    assert estimate is not None
    assert (estimate.quantity, estimate.model_id) == (Quantity.of("0.999702", "ratio"), "replicas")
    assert Evidence("configuration.failure_independence", "independent") in estimate.inputs
    assert Evidence("replica_availability", "0.99") in estimate.inputs


@pytest.mark.parametrize(
    ("changes", "missing"),
    [
        ({"failure_independence": "correlated"}, "independent.failure_independence"),
        ({"failure_independence": "unknown"}, "configuration.failure_independence"),
        ({"failure_independence": None}, "configuration.failure_independence"),
        ({"failover_mode": "manual"}, "automatic.failover_mode"),
        ({"failover_mode": "none"}, "automatic.failover_mode"),
        ({"failover_mode": None}, "configuration.failover_mode"),
        ({"min_healthy_replicas": None}, "configuration.min_healthy_replicas"),
        ({"min_healthy_replicas": 4}, "consistent.min_healthy_replicas"),
        ({"replica_availability": None}, "configuration.replica_availability"),
    ],
)
def test_redundancy_is_not_combined_without_its_assumptions(changes: dict[str, Any], missing: str) -> None:
    values = {k: v for k, v in (REDUNDANT | changes).items() if v is not None}
    component = one(**values)
    estimate = component.estimate("availability")
    assert estimate is not None
    assert (estimate.quantity, estimate.missing) == (None, (missing,))


# --- recovery and data loss ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected", "basis"),
    [
        (
            {"failover_mode": "automatic", "failover_seconds": 30, "mttr_seconds": 3600},
            "30",
            "failover_seconds (declared failover)",
        ),
        ({"failover_mode": "manual", "failover_seconds": 900}, "900", "failover_seconds (declared failover)"),
        (
            {"failover_mode": "none", "failover_seconds": 5, "mttr_seconds": 1800},
            "1800",
            "mttr_seconds (repair)",
        ),
        ({"mttr_seconds": Decimal("0.5")}, "0.5", "mttr_seconds (repair)"),
    ],
)
def test_recovery_time(values: dict[str, Any], expected: str, basis: str) -> None:
    estimate = one(**values).estimate("recovery_time")
    assert estimate is not None
    assert (estimate.quantity, estimate.basis) == (Quantity.of(expected, "s"), basis)


def test_recovery_time_without_data_is_unknown() -> None:
    estimate = one(failover_mode="automatic").estimate("recovery_time")
    assert estimate is not None
    assert (estimate.quantity, estimate.missing) == (
        None,
        ("configuration.failover_seconds", "configuration.mttr_seconds"),
    )


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"replication_mode": "synchronous", "backup_interval_seconds": 3600}, "0"),
        (
            {
                "replication_mode": "asynchronous",
                "replication_lag_seconds": 5,
                "backup_interval_seconds": 3600,
            },
            "5",
        ),
        ({"replication_mode": "asynchronous", "backup_interval_seconds": 3600}, "3600"),
        ({"backup_enabled": True, "backup_interval_seconds": 86400}, "86400"),
    ],
)
def test_data_loss_window(values: dict[str, Any], expected: str) -> None:
    estimate = one(NodeKind.DATABASE, **values).estimate("data_loss_window")
    assert estimate is not None
    assert estimate.quantity == Quantity.of(expected, "s")


def test_data_loss_without_a_declared_mechanism_is_unknown() -> None:
    for values, missing in (
        ({}, ("configuration.backup_interval_seconds", "configuration.replication_mode")),
        (
            {"replication_mode": "asynchronous"},
            ("configuration.backup_interval_seconds", "configuration.replication_lag_seconds"),
        ),
        (
            {"backup_enabled": False, "backup_interval_seconds": 3600},
            ("configuration.backup_interval_seconds", "configuration.replication_mode"),
        ),
    ):
        estimate = one(NodeKind.DATABASE, **values).estimate("data_loss_window")
        assert estimate is not None
        assert (estimate.quantity, estimate.missing) == (None, missing), values


# --- end to end ----------------------------------------------------------------------------------


def test_the_shipped_models_feed_the_paths() -> None:
    sync: dict[str, Any] = {
        "kind": ConnectionKind.REQUEST,
        "protocol": "https",
        "interaction": Interaction.SYNCHRONOUS,
    }
    result = run(
        node("lb", NodeKind.LOAD_BALANCER, configuration=Configuration({"availability": Decimal("0.9999")})),
        node("api", configuration=Configuration(REDUNDANT)),
        links=(connection("web-lb", "web", "lb", **sync), connection("lb-api", "lb", "api", **sync)),
    )
    [path] = result.paths
    assert path.availability.quantity == Quantity.of("0.999602030", "ratio")  # 0.9999 x 0.999702
    assert {m for m, _ in result.model_set.models} >= {
        "mtbf-mttr",
        "replicas",
        "request-paths",
        "topology-findings",
    }


def test_components_nothing_is_declared_for_say_what_is_missing() -> None:
    component = one()
    assert component.status is ComponentStatus.INSUFFICIENT_INPUT
    assert set(component.missing) >= {
        "configuration.availability",
        "configuration.mtbf_seconds",
        "configuration.replicas",
    }
    storage = one(NodeKind.STORAGE)
    assert set(storage.missing) >= {"configuration.availability"}
    assert all(e.quantity is None for e in (*component.estimates, *storage.estimates))
