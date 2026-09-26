"""Growth scenarios and scaling options (Milestone 7, phase 6): explicit changes, stated scaling,
comparable with the baseline."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.errors import InvalidScenario
from core.domain.capacity.results import BottleneckCondition, CapacityResult, Utilization
from core.domain.capacity.scenarios import ConfigurationChange, ScalingKind, Scenario, ScenarioResult
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.validation.options import RevisionInfo
from engines.capacity.context import CapacityContext
from engines.capacity.engine import analyze
from engines.capacity.operating_envelope import run_scenario, scenario_workload
from engines.capacity.registry import default_registry
from engines.capacity.traffic import propagate
from tests.unit.architecture_ir.builders import connection, node

REVISION = RevisionInfo("arch-1", 1, "a" * 64)
RPS = "requests/second"


def architecture(api: dict[str, Any], gw: dict[str, Any] | None = None) -> ArchitectureIR:
    link = {"kind": ConnectionKind.REQUEST, "protocol": "https", "interaction": Interaction.SYNCHRONOUS}
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            node(
                "gw",
                NodeKind.GATEWAY,
                configuration=Configuration(gw or {"throughput_limit_per_second": 100_000}),
            ),
            node("api", configuration=Configuration(api)),
        ),
        connections=(
            connection("web-gw", "web", "gw", configuration=Configuration({"traffic_ratio": 1}), **link),
            connection("gw-api", "gw", "api", configuration=Configuration({"traffic_ratio": 1}), **link),
        ),
    )


REPLICATED = {"replicas": 4, "throughput_per_replica_per_second": 300}  # 1200 rps


def baseline(
    ir: ArchitectureIR, peak: str = "1000", target: str | None = None
) -> tuple[CapacityContext, CapacityResult]:
    load = WorkloadProfile(
        "Peak",
        WorkloadType.REQUEST_RESPONSE,
        peak_rate=Quantity.of(peak, RPS),
        average_rate=Quantity.of("400", RPS),
        target_utilization=Decimal(target) if target else None,
    )
    context = CapacityContext(ir, REVISION, AnalysisRequest(uuid.UUID(int=1), 1, load))
    return context, analyze(context, default_registry(), propagate)


def scenario(ir: ArchitectureIR, s: Scenario, **kwargs: Any) -> ScenarioResult:
    context, result = baseline(ir, **kwargs)
    return run_scenario(context, result, s, default_registry(), propagate)


def api_throughput(result: CapacityResult) -> Utilization:
    api = next(c for c in result.components if c.node_id == "api")
    return next(u for u in api.utilization if u.resource == "work_rate")


def test_a_neutral_scenario_equals_the_baseline() -> None:
    ir = architecture(REPLICATED)
    _, base = baseline(ir)
    same = scenario(ir, Scenario("Same"))
    assert same.result == base
    assert same.comparison.new_bottlenecks == ()
    assert all(d.utilization_before == d.utilization_after for d in same.comparison.resources)


def test_growth_scales_demand_and_finds_what_breaks() -> None:
    doubled = scenario(architecture(REPLICATED), Scenario("Double", growth=Decimal(2)))
    u = api_throughput(doubled.result)
    assert (u.demand, u.capacity, u.ratio) == (
        Quantity.of("2000", RPS),
        Quantity.of("1200", RPS),
        Decimal("1.666666667"),
    )
    assert (
        "api",
        "work_rate",
        BottleneckCondition.EXCEEDS_CAPACITY.value,
    ) in doubled.comparison.new_bottlenecks
    assert ("workload_multiplier", "2") in {(e.label, e.value) for e in doubled.comparison.changed_inputs}
    [option] = [o for o in doubled.scaling if o.resource == "work_rate"]
    assert (option.kind, option.current, option.required) == (
        ScalingKind.HORIZONTAL,
        Quantity.of("4", "replicas"),
        Quantity.of("7", "replicas"),  # ceil(2000 / 300)
    )
    assert "linear" in option.basis


def test_compound_growth_and_a_target_rate() -> None:
    ir = architecture(REPLICATED)
    compound = scenario(ir, Scenario("A year", growth_rate=Decimal("0.1"), periods=2))
    assert api_throughput(compound.result).demand == Quantity.of("1210", RPS)  # 1000 x 1.1^2
    target = scenario(ir, Scenario("Launch", target_rate=Quantity.of("3000", RPS)))
    assert api_throughput(target.result).demand == Quantity.of("3000", RPS)
    context, _ = baseline(ir)
    assert scenario_workload(
        context.workload, Scenario("Launch", target_rate=Quantity.of("3000", RPS))
    ).average_rate == (
        Quantity.of("1200", RPS)  # the average keeps its ratio to the peak
    )


def test_reduced_workload_resolves_bottlenecks() -> None:
    ir = architecture(REPLICATED)
    busy = scenario(ir, Scenario("Half", growth=Decimal("0.5")), peak="2000")
    assert ("api", "work_rate", "exceeds_capacity") in busy.comparison.resolved_bottlenecks
    assert busy.result.bottlenecks == ()


def test_a_resource_increase_is_an_explicit_change() -> None:
    ir = architecture(REPLICATED)
    more = scenario(
        ir,
        Scenario(
            "Scale out", growth=Decimal(2), changes=(ConfigurationChange("api", "replicas", Decimal(7)),)
        ),
    )
    assert api_throughput(more.result).capacity == Quantity.of("2100", RPS)
    assert more.result.bottlenecks == ()
    assert ("api.replicas", "7") in {(e.label, e.value) for e in more.comparison.changed_configuration}
    [delta] = [d for d in more.comparison.resources if (d.node_id, d.resource) == ("api", "work_rate")]
    assert (delta.capacity_before, delta.capacity_after) == (Decimal(1200), Decimal(2100))


def test_a_declared_total_does_not_scale_by_itself() -> None:
    fixed = scenario(
        architecture({"replicas": 4, "throughput_limit_per_second": 1200}),
        Scenario("Double", growth=Decimal(2)),
    )
    assert api_throughput(fixed.result).capacity == Quantity.of("1200", RPS)  # replicas do not change it
    assert [o for o in fixed.scaling if o.node_id == "api"] == []
    assert [(u.element_id, u.code) for u in fixed.unsupported_scaling] == [("api", "scaling_unsupported")]


def test_cpu_scaling_options_are_vertical_or_horizontal() -> None:
    config = {
        "replicas": 2,
        "cpu_limit_cores": 1,
        "cpu_core_seconds_per_request": Decimal("0.003"),
        "throughput_limit_per_second": 10**6,
    }
    result = scenario(architecture(config), Scenario("Same"), target="0.75")
    options = {(o.kind, o.resource): o for o in result.scaling}
    assert options[(ScalingKind.VERTICAL, "cpu")].required == Quantity.of("2", "cores")  # 3 / (2 x 0.75)
    assert options[(ScalingKind.HORIZONTAL, "cpu")].required == Quantity.of("4", "replicas")  # ceil(3 / 0.75)


@pytest.mark.parametrize(
    ("build", "field", "reason"),
    [
        (lambda: Scenario("x", growth=Decimal(0)), "growth", "out_of_range"),
        (lambda: Scenario("x", growth=Decimal(-1)), "growth", "negative"),
        (
            lambda: Scenario("x", growth=Decimal(2), target_rate=Quantity.of("5", RPS)),
            "growth",
            "more_than_one_workload_change",
        ),
        (lambda: Scenario("x", growth_rate=Decimal("0.1")), "periods", "required"),
        (lambda: Scenario("x", growth_rate=Decimal("0.1"), periods=0), "periods", "out_of_range"),
        (lambda: Scenario("x", target_utilization=Decimal("1.2")), "target_utilization", "out_of_range"),
        (lambda: Scenario("!bad"), "name", "invalid_name"),
        (lambda: ConfigurationChange("api", "technology", Decimal(1)), "changes.property", "not_changeable"),
        (
            lambda: Scenario("x", changes=(ConfigurationChange("api", "replicas", Decimal(2)),) * 2),
            "changes",
            "duplicate_change",
        ),
    ],
)
def test_invalid_scenarios_are_refused(build: Any, field: str, reason: str) -> None:
    with pytest.raises(InvalidScenario) as raised:
        build()
    assert raised.value.details == {"field": field, "reason": reason}


@pytest.mark.parametrize(
    ("s", "field", "reason"),
    [
        (
            Scenario("x", changes=(ConfigurationChange("ghost", "replicas", Decimal(2)),)),
            "changes.element_id",
            "not_found",
        ),
        (
            Scenario("x", changes=(ConfigurationChange("api", "pool_size", Decimal(2)),)),
            "changes.property",
            "not_changeable_here",
        ),
        (
            Scenario("x", changes=(ConfigurationChange("api", "replicas", Decimal("2.5")),)),
            "changes.value",
            "not_a_whole_number",
        ),
        (
            Scenario("x", changes=(ConfigurationChange("gw-api", "traffic_ratio", Decimal(2)),)),
            "changes.value",
            "out_of_range",
        ),
        (Scenario("x", target_rate=Quantity.of("5", "events/second")), "target_rate", "wrong_dimension"),
    ],
)
def test_scenarios_that_cannot_apply_are_refused(s: Scenario, field: str, reason: str) -> None:
    with pytest.raises(InvalidScenario) as raised:
        scenario(architecture(REPLICATED), s)
    assert raised.value.details == {"field": field, "reason": reason}


def test_load_distribution_changes_are_explicit() -> None:
    shifted = scenario(
        architecture(REPLICATED),
        Scenario("Half to api", changes=(ConfigurationChange("gw-api", "traffic_ratio", Decimal("0.5")),)),
    )
    assert api_throughput(shifted.result).demand == Quantity.of("500", RPS)


def test_scenarios_are_deterministic_and_leave_the_baseline_untouched() -> None:
    ir = architecture(REPLICATED)
    s = Scenario("Double", growth=Decimal(2), changes=(ConfigurationChange("api", "replicas", Decimal(5)),))
    first, again = scenario(ir, s), scenario(ir, s)
    assert first == again
    assert first.to_dict() == again.to_dict()
    assert next(n for n in ir.nodes if n.id == "api").configuration.values["replicas"] == 4
    assert Scenario.from_dict(s.to_dict()) == s
