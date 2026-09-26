"""The capacity model contract, registry and orchestrator (Milestone 7, phase 2), with stand-in
models: declared limits, a model needing workload inputs, and broken ones."""

import dataclasses
import logging
import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.errors import InvalidCapacityConfig
from core.domain.capacity.results import AnalysisStatus, ComponentStatus, Demand, Estimate, Source
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadAssumption, WorkloadProfile, WorkloadType
from core.domain.parameters import ParamSpec, ParamType
from core.domain.validation.options import RevisionInfo
from engines.capacity.context import CapacityContext
from engines.capacity.engine import (
    CapacityModel,
    DuplicateModel,
    ModelInputs,
    ModelMeta,
    ModelOutput,
    Propagation,
    Registry,
    analyze,
    missing_inputs,
)
from tests.unit.architecture_ir.builders import api_and_postgres, node

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
WORKLOAD = WorkloadProfile(
    "Peak", WorkloadType.REQUEST_RESPONSE, peak_rate=Quantity.of("100", "requests/second")
)
COMPUTE = frozenset({NodeKind.SERVICE, NodeKind.WORKER, NodeKind.GATEWAY})


class ReplicaCount:
    """Reports the declared replica count as a resource (needs ``replicas``)."""

    meta = ModelMeta(
        "test.replicas",
        1,
        "Replicas",
        "The declared replica count.",
        COMPUTE | {NodeKind.DATABASE},
        ("instances",),
        configuration=("replicas",),
        parameters={"scale": ParamSpec(ParamType.INTEGER, "A multiplier.", default=1, minimum=1, maximum=10)},
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        replicas = inputs.node.configuration.values["replicas"] * inputs.parameters["scale"]
        return ModelOutput(
            resources=(
                Estimate(
                    inputs.node.id,
                    "instances",
                    Quantity.of(replicas, "connections"),
                    Source.DECLARED,
                    "configuration.replicas",
                    self.meta.id,
                    self.meta.version,
                ),
            )
        )


class NeedsReadRatio:
    meta = ModelMeta(
        "test.reads",
        1,
        "Reads",
        "Needs the read ratio and a cache assumption.",
        frozenset({NodeKind.DATABASE}),
        ("operation_rate",),
        workload=("read_ratio",),
        assumptions=("cache_hit_ratio",),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        return ModelOutput(
            limits=(
                Estimate(
                    inputs.node.id,
                    "operation_rate",
                    None,
                    Source.UNKNOWN,
                    "No operation limit is declared.",
                    missing=("configuration.max_operations_per_second",),
                ),
            )
        )


class Crashes:
    meta = ModelMeta("test.crashes", 1, "Crashes", "Always fails.", COMPUTE, ("cpu",))

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        raise RuntimeError("boom: internal detail")


class Malformed:
    meta = ModelMeta("test.malformed", 1, "Malformed", "Reports about another node.", COMPUTE, ("cpu",))

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        return ModelOutput(
            resources=(Estimate("someone-else", "cpu", None, Source.UNKNOWN, "x", missing=("y",)),)
        )


def context(
    ir: ArchitectureIR | None = None, workload: WorkloadProfile = WORKLOAD, **request: Any
) -> CapacityContext:
    return CapacityContext(
        ir or api_and_postgres(), REVISION, AnalysisRequest(uuid.UUID(int=1), 1, workload, **request)
    )


def by_node(result: Any) -> dict[str, Any]:
    return {c.node_id: c for c in result.components}


# --- registry --------------------------------------------------------------------------------------


def test_registration_resolution_and_duplicates() -> None:
    registry = Registry([NeedsReadRatio(), ReplicaCount()])
    assert [m.meta.id for m in registry.models()] == ["test.reads", "test.replicas"]
    assert [m.meta.id for m in registry.models(kind=NodeKind.SERVICE)] == ["test.replicas"]
    assert registry.get("test.reads") is not None
    assert registry.get("test.nope") is None
    with pytest.raises(DuplicateModel):
        registry.register(ReplicaCount())
    assert registry.model_set(registry.models()) == Registry([ReplicaCount(), NeedsReadRatio()]).model_set(
        registry.models()
    )


@pytest.mark.parametrize(
    ("request_options", "reason"),
    [
        ({"models": ("test.nope",)}, "unknown_model"),
        ({"models": ("test.reads",), "parameters": {"test.replicas": {"scale": 2}}}, "model_not_selected"),
        ({"parameters": {"test.replicas": {"speed": 2}}}, "unknown_parameter"),
        ({"parameters": {"test.replicas": {"scale": 0}}}, "out_of_range"),
        ({"parameters": {"test.replicas": {"scale": "2"}}}, "not_an_integer"),
    ],
)
def test_invalid_selection_is_refused_before_anything_runs(
    request_options: dict[str, Any], reason: str
) -> None:
    with pytest.raises(InvalidCapacityConfig) as raised:
        analyze(context(**request_options), Registry([NeedsReadRatio(), ReplicaCount()]))
    assert raised.value.details["reason"] == reason


# --- execution -------------------------------------------------------------------------------------


def test_models_run_on_the_nodes_they_support_with_their_parameters() -> None:
    result = analyze(context(parameters={"test.replicas": {"scale": 2}}), Registry([ReplicaCount()]))
    components = by_node(result)
    assert set(components) == {"api", "db"}  # the web client is a demand source, not in scope
    api = components["api"]
    assert (api.status, api.models) == (ComponentStatus.ESTIMATED, (("test.replicas", 1),))
    assert api.resources[0].quantity == Quantity.of(6, "connections")  # 3 replicas x scale 2
    assert components["db"].status is ComponentStatus.INSUFFICIENT_INPUT
    assert components["db"].missing == ("configuration.replicas",)
    assert result.status is AnalysisStatus.PARTIAL


def test_missing_workload_fields_and_assumptions_are_named() -> None:
    result = analyze(context(), Registry([NeedsReadRatio()]))
    db = by_node(result)["db"]
    assert db.status is ComponentStatus.INSUFFICIENT_INPUT
    assert db.missing == ("assumptions.cache_hit_ratio", "workload.read_ratio")
    with_inputs = WorkloadProfile(
        "Peak",
        WorkloadType.REQUEST_RESPONSE,
        peak_rate=Quantity.of("100", "requests/second"),
        read_ratio=Decimal("0.8"),
        assumptions=(WorkloadAssumption("cache_hit_ratio", "80 % hit.", Quantity.of("0.8", "ratio")),),
    )
    ran = by_node(analyze(context(workload=with_inputs), Registry([NeedsReadRatio()])))["db"]
    assert ran.missing == ("configuration.max_operations_per_second",)  # what the model itself lacks


def test_an_unknown_configuration_value_counts_as_missing() -> None:
    ir = ArchitectureIR("One", nodes=(node("svc", configuration=Configuration(unknown={"replicas"})),))
    meta = ReplicaCount().meta
    assert missing_inputs(meta, ir.nodes[0], context(ir)) == ("configuration.replicas",)


def test_a_node_without_a_model_is_unsupported_not_zero() -> None:
    ir = ArchitectureIR("Q", nodes=(node("q", NodeKind.QUEUE),))
    result = analyze(context(ir), Registry([ReplicaCount()]))
    [queue] = result.components
    assert (queue.status, queue.limits, queue.resources) == (ComponentStatus.UNSUPPORTED, (), ())
    assert [(u.element_id, u.code) for u in result.unsupported] == [("q", "no_model")]
    assert result.status is AnalysisStatus.UNSUPPORTED


def test_a_failing_model_is_recorded_and_the_others_still_count(caplog: pytest.LogCaptureFixture) -> None:
    registry = Registry([Crashes(), Malformed(), ReplicaCount()])
    with caplog.at_level(logging.ERROR, logger="architectos.capacity"):
        result = analyze(context(), registry)
    api = by_node(result)["api"]
    assert (api.status, api.models) == (ComponentStatus.ESTIMATED, (("test.replicas", 1),))
    codes = {(u.element_id, u.code) for u in result.unsupported}
    assert {("api", "model_failed"), ("api", "invalid_output")} <= codes
    assert all("boom" not in u.message for u in result.unsupported)  # no internals
    assert any(getattr(r, "model_id", None) == "test.crashes" for r in caplog.records)


def test_a_node_whose_only_models_fail_is_unsupported() -> None:
    result = analyze(context(), Registry([Crashes()]))
    assert by_node(result)["api"].status is ComponentStatus.UNSUPPORTED
    assert by_node(result)["api"].models == ()


def test_an_empty_architecture_says_so() -> None:
    result = analyze(context(ArchitectureIR("Empty")), Registry([ReplicaCount()]))
    assert (result.components, result.status) == ((), AnalysisStatus.UNSUPPORTED)
    assert {x.code for x in result.limitations} == {"catalog_unavailable", "no_measurements", "no_components"}


def test_demand_from_the_propagation_step_reaches_the_models() -> None:
    seen: list[tuple[Demand, ...]] = []

    class Spy:
        meta = ReplicaCount().meta

        def estimate(self, inputs: ModelInputs) -> ModelOutput:
            seen.append(inputs.demand)
            return ReplicaCount().estimate(inputs)

    demand = Demand("api", "request_rate", Quantity.of("100", "requests/second"), ("web", "web-api", "api"))

    def propagate(ctx: CapacityContext) -> Propagation:
        return Propagation(
            nodes={"api": (demand,)}, connections=(dataclasses.replace(demand, element_id="web-api"),)
        )

    spy: CapacityModel = Spy()
    result = analyze(context(), Registry([spy]), propagate)
    assert (demand,) in seen
    assert by_node(result)["api"].demand == (demand,)
    assert [d.element_id for d in result.connections] == ["web-api"]


def test_results_are_deterministic_whatever_the_registration_order() -> None:
    models: list[CapacityModel] = [NeedsReadRatio(), ReplicaCount(), Crashes()]
    one = analyze(context(), Registry(models))
    other = analyze(context(), Registry(models[::-1]))
    assert one == other
    assert one.fingerprint == other.fingerprint
    assert analyze(context(), Registry(models)).fingerprint == one.fingerprint


def test_the_context_fingerprint_follows_the_inputs() -> None:
    base = context()
    assert base.fingerprint == context().fingerprint
    assert base.fingerprint != context(parameters={"test.replicas": {"scale": 2}}).fingerprint
    other_workload = WorkloadProfile(
        "Peak", WorkloadType.REQUEST_RESPONSE, peak_rate=Quantity.of("101", "requests/second")
    )
    assert base.fingerprint != context(workload=other_workload).fingerprint
