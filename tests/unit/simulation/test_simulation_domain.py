"""The simulation domain contract (Milestone 12, phase 1): typed and validated scenarios and requests,
explicit units and semantics, unknown and unsupported kept as such, deterministic serialization."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.engine_results import Evidence, Limitation, ModelSet, Unsupported
from core.domain.simulations.entities import (
    PENDING,
    PricingInputs,
    Simulation,
    SimulationAssumption,
    SimulationError,
    SimulationRequest,
)
from core.domain.simulations.errors import (
    InvalidSimulationRequest,
    InvalidSimulationResult,
    InvalidSimulationTransition,
)
from core.domain.simulations.results import (
    SYSTEM,
    AnalysisRun,
    ComponentOutcome,
    Delta,
    EntryImpact,
    SimulationResult,
)
from core.domain.simulations.scenarios import ConfigurationChange, Failure, Scenario, WorkloadChange
from core.domain.simulations.values import AnalysisKind, FailureKind, Impact, RunState, SimulationStatus

A = AnalysisKind
F = "f" * 64
MODELS = ModelSet.of([("rps", 1)])
NOW = datetime(2026, 9, 27, tzinfo=UTC)
RPS = "requests/second"


def reason(error: pytest.ExceptionInfo[InvalidSimulationRequest]) -> tuple[str, str]:
    return error.value.details["field"], error.value.details["reason"]


# --- scenarios -----------------------------------------------------------------------------------


def test_a_workload_change_is_validated_by_the_capacity_engine() -> None:
    growth = WorkloadChange(growth=Decimal("2.0"))
    assert (growth.growth, growth.multiplier) == (Decimal(2), Decimal(2))
    compound = WorkloadChange(growth_rate=Decimal("0.1"), periods=2)
    assert compound.multiplier == Decimal("1.21")
    target = WorkloadChange(target_rate=Quantity.of(500, RPS))
    assert target.to_dict()["target_rate"] == {"value": "500", "unit": RPS}
    cases: list[tuple[dict[str, Any], tuple[str, str]]] = [
        ({}, ("scenario.workload", "empty")),
        (
            {"growth": 2, "target_rate": Quantity.of(5, RPS)},
            ("scenario.workload.growth", "more_than_one_workload_change"),
        ),
        ({"growth_rate": Decimal("0.1")}, ("scenario.workload.periods", "required")),
        ({"growth": 0}, ("scenario.workload.growth", "out_of_range")),
        ({"target_utilization": 2}, ("scenario.workload.target_utilization", "out_of_range")),
    ]
    for kwargs, expected in cases:
        with pytest.raises(InvalidSimulationRequest) as raised:
            WorkloadChange(**kwargs)
        assert reason(raised) == expected, kwargs


def test_units_are_explicit_and_never_guessed() -> None:
    with pytest.raises(InvalidSimulationRequest) as raised:
        WorkloadChange.from_dict({"target_rate": {"value": "5", "unit": "rpm-ish"}})
    assert reason(raised) == ("scenario.workload.target_rate.unit", "unknown_unit")
    with pytest.raises(InvalidSimulationRequest) as raised:
        WorkloadChange.from_dict({"target_rate": {"value": "-5", "unit": RPS}})
    assert reason(raised)[1] == "negative"


def test_configuration_changes_are_checked_against_the_ir_property() -> None:
    replicas = ConfigurationChange("api", "replicas", 6)
    cpu = ConfigurationChange.from_dict({"element_id": "api", "property": "cpu_limit_cores", "value": "1.50"})
    assert cpu.value == Decimal("1.5")
    assert cpu.to_dict() == {"element_id": "api", "property": "cpu_limit_cores", "value": "1.5"}
    assert ConfigurationChange("api", "replicas", None).value is None  # cleared: unknown, not 0
    assert replicas.key == ("api", "replicas")
    cases: list[tuple[str, str, Any, tuple[str, str]]] = [
        ("api", "colour", "blue", ("scenario.changes.property", "unknown_property")),
        ("api", "replicas", -1, ("scenario.changes.value", "invalid_value")),
        ("api", "replicas", 1.5, ("scenario.changes.value", "invalid_value")),
        ("api", "multi_az", "yes", ("scenario.changes.value", "invalid_value")),
        ("", "replicas", 2, ("scenario.changes.element_id", "invalid_reference")),
    ]
    for element, name, value, expected in cases:
        with pytest.raises(InvalidSimulationRequest) as raised:
            ConfigurationChange(element, name, value)
        assert reason(raised) == expected, (name, value)


def test_failures_name_an_element_or_a_declared_zone_or_region() -> None:
    assert Failure(FailureKind.ZONE, "eu-west-1a").to_dict() == {"kind": "zone", "target": "eu-west-1a"}
    with pytest.raises(InvalidSimulationRequest) as raised:
        Failure(FailureKind.REGION, "EU West")
    assert reason(raised) == ("scenario.failures.target", "invalid_value")
    with pytest.raises(InvalidSimulationRequest) as raised:
        Failure.from_dict({"kind": "meteor", "target": "api"})
    assert reason(raised) == ("scenario.failures.kind", "unknown_kind")


def test_a_scenario_is_canonical_and_refuses_conflicts() -> None:
    first = Scenario(
        "Launch",
        workload=WorkloadChange(growth=Decimal(2)),
        changes=(ConfigurationChange("db", "replicas", 3), ConfigurationChange("api", "replicas", 6)),
        failures=(Failure(FailureKind.CONNECTION, "api-db"), Failure(FailureKind.COMPONENT, "cache")),
    )
    assert [c.element_id for c in first.changes] == ["api", "db"]
    assert [f.target for f in first.failures] == ["cache", "api-db"]  # components before connections
    renamed = Scenario.from_dict(first.to_dict() | {"name": "Other name", "description": "Same content"})
    assert renamed.fingerprint == first.fingerprint  # name and description do not change what is asked
    assert Scenario.from_dict(first.to_dict()) == first
    twice = (ConfigurationChange("api", "replicas", 2), ConfigurationChange("api", "replicas", 3))
    failed_twice = (Failure(FailureKind.COMPONENT, "api"), Failure(FailureKind.COMPONENT, "api"))
    cases: list[tuple[dict[str, Any], tuple[str, str]]] = [
        ({}, ("scenario", "empty")),
        ({"changes": twice}, ("scenario.changes", "duplicate")),
        ({"failures": failed_twice}, ("scenario.failures", "duplicate")),
        (
            {"changes": twice[:1], "failures": failed_twice[:1]},
            ("scenario.changes", "changed_and_unavailable"),
        ),
        ({"changes": twice[:1] * 51}, ("scenario.changes", "too_many")),
    ]
    for kwargs, expected in cases:
        with pytest.raises(InvalidSimulationRequest) as raised:
            Scenario("S", **kwargs)
        assert reason(raised) == expected, kwargs
    with pytest.raises(InvalidSimulationRequest) as raised:
        Scenario.from_dict({"name": "S", "failures": [], "latency_ms": 5})
    assert reason(raised) == ("scenario.latency_ms", "unknown_field")  # no invented dimension


# --- the request ---------------------------------------------------------------------------------


def workload() -> WorkloadProfile:
    return WorkloadProfile("Peak", WorkloadType.REQUEST_RESPONSE, peak_rate=Quantity.of(100, RPS))


SCENARIO = Scenario("Double", workload=WorkloadChange(growth=Decimal(2)))
BASE: dict[str, Any] = {"architecture_id": uuid.UUID(int=1), "revision_number": 1, "scenario": SCENARIO}


def test_the_request_is_canonical_and_bounded() -> None:
    request = SimulationRequest(
        uuid.UUID(int=1),
        3,
        SCENARIO,
        analyses=(A.COST, A.CAPACITY, A.CAPACITY),
        workload=workload(),
        entries=("web", "api", "web"),
        pricing=PricingInputs(uuid.UUID(int=2), date(2026, 9, 1), Decimal("365.0")),
        assumptions=(SimulationAssumption("b", "B"), SimulationAssumption("a", "A")),
        label="Before launch",
    )
    assert request.analyses == (A.CAPACITY, A.COST)
    assert request.entries == ("api", "web")
    assert [a.key for a in request.assumptions] == ["a", "b"]
    inputs = request.inputs()
    assert inputs["pricing"] == {
        "snapshot_id": str(uuid.UUID(int=2)),
        "pricing_date": "2026-09-01",
        "operating_hours_per_month": "365",
    }
    assert inputs["scenario"] == SCENARIO.to_dict()
    same_key = (SimulationAssumption("a", "A"), SimulationAssumption("a", "B"))
    cases: list[tuple[dict[str, Any], tuple[str, str]]] = [
        ({"revision_number": 0}, ("revision_number", "not_a_positive_count")),
        ({"analyses": ()}, ("analyses", "empty")),
        ({"entries": ()}, ("entries", "invalid_entries")),
        ({"assumptions": same_key}, ("assumptions", "duplicate_key")),
        ({"label": " "}, ("label", "invalid_text")),
    ]
    for kwargs, expected in cases:
        with pytest.raises(InvalidSimulationRequest) as raised:
            SimulationRequest(**(BASE | kwargs))
        assert reason(raised) == expected, kwargs


def test_pricing_inputs_follow_the_cost_engines_rules() -> None:
    with pytest.raises(InvalidSimulationRequest) as raised:
        PricingInputs(uuid.UUID(int=2), date(2026, 9, 1), Decimal(800))
    assert reason(raised) == ("pricing.operating_hours_per_month", "out_of_range")
    cost = PricingInputs(uuid.UUID(int=2), date(2026, 9, 1)).cost_request(uuid.UUID(int=1), 4, "EUR")
    assert (cost.revision_number, cost.currency, cost.operating_hours_per_month) == (4, "EUR", Decimal(730))


# --- results -------------------------------------------------------------------------------------


def test_deltas_have_units_and_never_invent_a_difference() -> None:
    grew = Delta(A.CAPACITY, "api", "work_rate.demand", RPS, Decimal(100), Decimal(200))
    assert (grew.difference, grew.percentage, grew.comparable) == (Decimal(100), Decimal(100), True)
    from_zero = Delta(A.COST, SYSTEM, "monthly_cost", "USD/month", Decimal(0), Decimal(40))
    assert (from_zero.difference, from_zero.percentage) == (Decimal(40), None)  # no percentage of 0
    unknown = Delta(A.CAPACITY, "db", "cpu.utilization", "ratio", None, Decimal("0.5"))
    assert (unknown.difference, unknown.percentage, unknown.comparable) == (None, None, False)
    marked = Delta(A.COST, SYSTEM, "monthly_cost", "USD/month", Decimal(10), Decimal(12), "incomplete")
    assert (marked.difference, marked.comparable) == (None, False)
    thirds = Delta(A.CAPACITY, "api", "work_rate.demand", RPS, Decimal(3), Decimal(4))
    assert thirds.percentage == Decimal("33.333333333")  # half-even, 9 places
    assert Delta.from_dict(grew.to_dict()) == grew
    valid: dict[str, Any] = {
        "analysis": A.CAPACITY,
        "element_id": "api",
        "metric": "work_rate.demand",
        "unit": RPS,
        "baseline": Decimal(1),
        "scenario": Decimal(2),
    }
    for bad in ({"unit": ""}, {"metric": "Work Rate"}, {"baseline": Decimal("NaN")}, {"note": "Not a code"}):
        with pytest.raises(InvalidSimulationResult):
            Delta(**(valid | bad))


def test_impacts_and_runs_keep_unknown_and_unsupported_explicit() -> None:
    with pytest.raises(InvalidSimulationResult):
        EntryImpact("web", Impact.UNKNOWN, "Whether db fails over is not declared.")  # unknown needs missing
    unknown = EntryImpact("web", Impact.UNKNOWN, "Not declared.", ("db",), ("db.failover_mode",))
    assert EntryImpact.from_dict(unknown.to_dict()) == unknown
    with pytest.raises(InvalidSimulationResult):
        AnalysisRun(A.COST, RunState.UNSUPPORTED)  # an unsupported run says why
    with pytest.raises(InvalidSimulationResult):
        AnalysisRun(A.CAPACITY, RunState.COMPLETED, MODELS)  # a run that ran names both results
    unsupported = AnalysisRun(
        A.COST, RunState.UNSUPPORTED, reason="no_pricing_snapshot", message="No snapshot."
    )
    assert AnalysisRun.from_dict(unsupported.to_dict()) == unsupported


def run(state: RunState, analysis: AnalysisKind = A.CAPACITY) -> AnalysisRun:
    if state in {RunState.COMPLETED, RunState.PARTIAL}:
        return AnalysisRun(analysis, state, MODELS, "a" * 64, "b" * 64)
    return AnalysisRun(analysis, state, reason="no_workload")


def result(**fields: Any) -> SimulationResult:
    return SimulationResult(MODELS, F, F, **fields)


def test_the_status_follows_what_was_established() -> None:
    assert result().status is SimulationStatus.UNSUPPORTED
    assert result(runs=(run(RunState.UNSUPPORTED),)).status is SimulationStatus.UNSUPPORTED
    failed = (run(RunState.FAILED), run(RunState.UNSUPPORTED, A.COST))
    assert result(runs=failed).status is SimulationStatus.FAILED
    assert result(runs=(run(RunState.COMPLETED),)).status is SimulationStatus.COMPLETED
    failed_cost = (run(RunState.COMPLETED), run(RunState.FAILED, A.COST))
    assert result(runs=failed_cost).status is SimulationStatus.PARTIAL  # never completed when one failed
    unknown = EntryImpact("web", Impact.UNKNOWN, "Not declared.", ("db",), ("db.failover_mode",))
    reliability = (run(RunState.COMPLETED, A.RELIABILITY),)
    assert result(runs=reliability, entries=(unknown,)).status is SimulationStatus.PARTIAL
    gap = Unsupported("db", "not_modeled", "Not modeled.")
    assert result(runs=(run(RunState.COMPLETED),), unsupported=(gap,)).status is SimulationStatus.PARTIAL


def test_results_are_ordered_counted_fingerprinted_and_round_trip() -> None:
    first = result(
        runs=(run(RunState.PARTIAL, A.RELIABILITY), run(RunState.COMPLETED)),
        components=(
            ComponentOutcome("db", unavailable=True, impact=Impact.INTERRUPTED),
            ComponentOutcome("api", changes=(Evidence("api.replicas", "2 -> 6"),)),
        ),
        entries=(EntryImpact("web", Impact.INTERRUPTED, "web requires db.", ("db",)),),
        deltas=(
            Delta(A.CAPACITY, "db", "cpu.utilization", "ratio", Decimal("0.5"), Decimal("0.9")),
            Delta(A.CAPACITY, "api", "work_rate.demand", RPS, Decimal(100), Decimal(100)),
        ),
        assumptions=(Evidence("assumption.b", "B"), Evidence("assumption.a", "A")),
        trace=(Evidence("overlay.1", "api.replicas 2 -> 6"), Evidence("overlay.0", "db unavailable")),
        limitations=(Limitation("model_based", "A projection."), Limitation("model_based", "A projection.")),
    )
    assert [r.analysis for r in first.runs] == [A.CAPACITY, A.RELIABILITY]
    assert [c.node_id for c in first.components] == ["api", "db"]
    assert [d.element_id for d in first.deltas] == ["api", "db"]
    assert [e.label for e in first.trace] == ["overlay.1", "overlay.0"]  # the trace keeps its order
    assert len(first.limitations) == 1
    assert first.summary() == {
        "runs": {"completed": 1, "partial": 1, "unsupported": 0, "failed": 0},
        "entries": {"unaffected": 0, "tolerated": 0, "degraded": 0, "interrupted": 1, "unknown": 0},
        "components": 2,
        "unavailable": 1,
        "deltas": {"comparable": 2, "not_comparable": 0, "changed": 1},
        "unsupported": 0,
    }
    again = SimulationResult.from_dict(first.to_dict())
    assert again == first
    assert again.fingerprint == first.fingerprint
    assert "score" not in first.to_dict()["summary"]
    with pytest.raises(InvalidSimulationResult):
        result(runs=(run(RunState.COMPLETED), run(RunState.COMPLETED)))  # one run per analysis
    with pytest.raises(InvalidSimulationResult):
        result(components=(ComponentOutcome("api"), ComponentOutcome("api")))


def test_the_lifecycle_ends_in_what_the_result_established() -> None:
    simulation = Simulation(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "c" * 64, PENDING, None, NOW)
    running = simulation.start(NOW)
    done = running.finish(result(runs=(run(RunState.COMPLETED),)), NOW)
    assert (done.status, done.finished) == ("completed", True)
    with pytest.raises(InvalidSimulationTransition):
        done.start(NOW)
    failed = simulation.fail(SimulationError("engine_error", "The engine could not complete it."), NOW)
    assert failed.status == "failed"
