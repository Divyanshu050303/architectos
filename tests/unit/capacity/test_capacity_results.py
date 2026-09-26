"""The capacity result contract and the analysis lifecycle (Milestone 7, phase 1)."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.domain.capacity.analyses import AnalysisError, AnalysisRequest, CapacityAnalysis
from core.domain.capacity.errors import InvalidAnalysisTransition, InvalidCapacityResult, InvalidWorkload
from core.domain.capacity.results import (
    AnalysisStatus,
    Bottleneck,
    BottleneckCondition,
    CapacityResult,
    Certainty,
    ComponentResult,
    ComponentStatus,
    Demand,
    Estimate,
    Evidence,
    Limitation,
    ModelSet,
    Source,
    Unsupported,
    Utilization,
    UtilizationState,
    derive_status,
)
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadAssumption, WorkloadProfile, WorkloadType

RPS = "requests/second"
NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
MODELS = ModelSet.of([("declared-throughput", 1)])


def rps(value: str) -> Quantity:
    return Quantity.of(value, RPS)


def limit(node: str = "api", value: str | None = "1000") -> Estimate:
    if value is None:
        return Estimate(node, "request_rate", None, Source.UNKNOWN, "No throughput is declared.",
                        missing=("configuration.max_requests_per_second",))  # fmt: skip
    return Estimate(
        node, "request_rate", rps(value), Source.DECLARED, "configuration.max_requests_per_second"
    )


def component(
    node: str = "api", status: ComponentStatus = ComponentStatus.ESTIMATED, **kwargs: Any
) -> ComponentResult:
    model: dict[str, Any] = (
        {}
        if status is ComponentStatus.UNSUPPORTED
        else {"model_id": "declared-throughput", "model_version": 1}
    )
    missing: dict[str, Any] = (
        {"missing": ("configuration.max_requests_per_second",)}
        if status is ComponentStatus.INSUFFICIENT_INPUT
        else {}
    )
    return ComponentResult(node, status, **(model | missing | kwargs))


# --- utilization ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("demand", "capacity", "state", "ratio", "headroom"),
    [
        ("500", "1000", UtilizationState.BELOW, Decimal("0.5"), Decimal(500)),
        ("1000", "1000", UtilizationState.AT, Decimal(1), Decimal(0)),
        ("1500", "1000", UtilizationState.ABOVE, Decimal("1.5"), Decimal(-500)),  # never clamped
        ("5", "0", UtilizationState.NO_CAPACITY, None, Decimal(-5)),  # no division by zero
        ("0", "1000", UtilizationState.IDLE, Decimal(0), Decimal(1000)),
        (None, "1000", UtilizationState.UNKNOWN, None, None),
        ("500", None, UtilizationState.UNKNOWN, None, None),
    ],
)
def test_utilization_and_headroom(
    demand: str | None,
    capacity: str | None,
    state: UtilizationState,
    ratio: Decimal | None,
    headroom: Decimal | None,
) -> None:
    u = Utilization("request_rate", rps(demand) if demand else None, rps(capacity) if capacity else None)
    assert (u.state, u.ratio, u.headroom) == (state, ratio, headroom)
    assert u.relative_headroom == (None if ratio is None else 1 - ratio)


def test_utilization_compares_in_one_unit() -> None:
    u = Utilization(
        "request_rate", Quantity.of("60000", "requests/minute"), rps("2000"), target=Decimal("0.7")
    )
    assert u.ratio == Decimal("0.5")
    assert u.headroom_to_target == Decimal(400)  # 0.7 * 2000 - 1000
    assert Utilization("request_rate", rps("1"), rps("3")).ratio == Decimal("0.333333333")
    with pytest.raises(InvalidCapacityResult):
        Utilization("request_rate", rps("1"), Quantity.of("1", "MB/s"))


# --- estimates: unknown is honest ----------------------------------------------------------------


def test_an_unknown_value_has_no_number_and_names_what_is_missing() -> None:
    unknown = limit(value=None)
    assert (unknown.known, unknown.quantity, unknown.missing) == (
        False,
        None,
        ("configuration.max_requests_per_second",),
    )
    with pytest.raises(InvalidCapacityResult):  # a number cannot be "unknown"
        Estimate("api", "request_rate", rps("0"), Source.UNKNOWN, "guess")
    with pytest.raises(InvalidCapacityResult):  # a known source needs a number
        Estimate("api", "request_rate", None, Source.DECLARED, "configuration.x")
    with pytest.raises(InvalidCapacityResult):  # an estimate names its model
        Estimate("api", "request_rate", rps("5"), Source.MODEL_ESTIMATE, "formula")


def test_components_state_why_they_have_no_estimate() -> None:
    with pytest.raises(InvalidCapacityResult):
        ComponentResult("api", ComponentStatus.INSUFFICIENT_INPUT, "declared-throughput", 1)  # missing what?
    with pytest.raises(InvalidCapacityResult):
        ComponentResult("api", ComponentStatus.ESTIMATED)  # estimated by which model?


# --- status ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([ComponentStatus.ESTIMATED, ComponentStatus.ESTIMATED], AnalysisStatus.COMPLETED),
        ([ComponentStatus.ESTIMATED, ComponentStatus.UNSUPPORTED], AnalysisStatus.PARTIAL),
        ([ComponentStatus.ESTIMATED, ComponentStatus.INSUFFICIENT_INPUT], AnalysisStatus.PARTIAL),
        (
            [ComponentStatus.INSUFFICIENT_INPUT, ComponentStatus.UNSUPPORTED],
            AnalysisStatus.INSUFFICIENT_INPUT,
        ),
        ([ComponentStatus.UNSUPPORTED], AnalysisStatus.UNSUPPORTED),
        ([], AnalysisStatus.UNSUPPORTED),
    ],
)
def test_the_status_says_what_was_established(
    statuses: list[ComponentStatus], expected: AnalysisStatus
) -> None:
    components = [component(f"n{i}", s) for i, s in enumerate(statuses)]
    assert derive_status(components) is expected


# --- bottlenecks -----------------------------------------------------------------------------------


def test_a_bottleneck_is_modeled_only_with_both_sides_known() -> None:
    known = Utilization("request_rate", rps("1500"), rps("1000"))
    Bottleneck(
        "api", "request_rate", BottleneckCondition.EXCEEDS_CAPACITY, Certainty.MODELED, known, "x", "y"
    )
    with pytest.raises(InvalidCapacityResult):
        Bottleneck(
            "api", "request_rate", BottleneckCondition.EXCEEDS_CAPACITY, Certainty.CANDIDATE, known, "x", "y"
        )
    unknown = Utilization("request_rate", rps("1500"), None)
    with pytest.raises(InvalidCapacityResult):
        Bottleneck(
            "db", "request_rate", BottleneckCondition.UNKNOWN_CAPACITY, Certainty.MODELED, unknown, "x", "y"
        )


def test_bottlenecks_are_ordered_modeled_then_worst_first() -> None:
    def b(node: str, demand: str | None, capacity: str | None, condition: BottleneckCondition) -> Bottleneck:
        u = Utilization("request_rate", rps(demand) if demand else None, rps(capacity) if capacity else None)
        certainty = Certainty.MODELED if demand and capacity else Certainty.CANDIDATE
        return Bottleneck(node, "request_rate", condition, certainty, u, "x", "y")

    result = CapacityResult(
        MODELS,
        "f" * 64,
        bottlenecks=(
            b("c", "900", None, BottleneckCondition.UNKNOWN_CAPACITY),
            b("a", "1200", "1000", BottleneckCondition.EXCEEDS_CAPACITY),
            b("b", "3000", "1000", BottleneckCondition.EXCEEDS_CAPACITY),
            b("d", "1000", "1000", BottleneckCondition.AT_CAPACITY),
        ),
    )
    assert [x.node_id for x in result.bottlenecks] == ["b", "a", "d", "c"]


# --- the whole result ------------------------------------------------------------------------------


def full_result(order: int = 1) -> CapacityResult:
    api = component(
        "api",
        demand=(
            Demand("api", "request_rate", rps("1500"), ("web", "api"), (Evidence("traffic_ratio", "1"),)),
        ),
        limits=(limit(),),
        utilization=(Utilization("request_rate", rps("1500"), rps("1000")),),
    )
    parts: list[Any] = [
        api,
        component("db", ComponentStatus.INSUFFICIENT_INPUT),
        component("web", ComponentStatus.UNSUPPORTED),
    ]
    return CapacityResult(
        MODELS,
        "f" * 64,
        components=tuple(parts[::order]),
        connections=(Demand("web-api", "request_rate", rps("1500"), ("web", "web-api")),),
        bottlenecks=(
            Bottleneck(
                "api",
                "request_rate",
                BottleneckCondition.EXCEEDS_CAPACITY,
                Certainty.MODELED,
                Utilization("request_rate", rps("1500"), rps("1000")),
                "Demand is 1.5x the declared limit.",
                "Raise the limit or add replicas.",
                assumptions=("peak_factor",),
            ),
        ),
        unsupported=(Unsupported("web", "no_model", "No capacity model applies to a client."),),
        limitations=(Limitation("catalog_unavailable", "No component catalog is available."),),
    )


def test_results_are_ordered_summarized_and_fingerprinted() -> None:
    one, other = full_result(), full_result(order=-1)
    assert one == other
    assert one.fingerprint == other.fingerprint
    assert [c.node_id for c in one.components] == ["api", "db", "web"]
    assert one.status is AnalysisStatus.PARTIAL
    summary = one.summary
    assert summary.components == {"estimated": 1, "insufficient_input": 1, "unsupported": 1}
    assert summary.bottlenecks == {"modeled": 1, "candidate": 0}
    assert summary.highest_utilization == Decimal("1.5")


def test_results_round_trip_exactly() -> None:
    result = full_result()
    again = CapacityResult.from_dict(result.to_dict())
    assert again == result
    assert again.fingerprint == result.fingerprint
    with pytest.raises(InvalidCapacityResult):
        CapacityResult.from_dict({"model_set": {"version": "x", "models": []}})


def test_the_model_set_version_follows_its_models() -> None:
    assert ModelSet.of([("a-model", 1), ("b-model", 1)]) == ModelSet.of([("b-model", 1), ("a-model", 1)])
    assert ModelSet.of([("a-model", 2)]).version != ModelSet.of([("a-model", 1)]).version


# --- the request and the lifecycle -----------------------------------------------------------------

WORKLOAD = WorkloadProfile("Peak", WorkloadType.REQUEST_RESPONSE, peak_rate=rps("2000"))


def test_a_request_names_the_exact_revision_and_its_inputs() -> None:
    request = AnalysisRequest(uuid.UUID(int=1), 3, WORKLOAD, models=("b-model", "a-model"))
    assert request.models == ("a-model", "b-model")
    inputs = request.inputs()
    assert (inputs["revision_number"], inputs["workload"]["name"]) == (3, "Peak")
    for bad in (
        {"revision_number": 0},
        {"revision_number": True},
        {"models": ("Bad Model",)},
        {"label": " "},
    ):
        fields: dict[str, Any] = {
            "architecture_id": uuid.UUID(int=1),
            "revision_number": 1,
            "workload": WORKLOAD,
        } | bad
        with pytest.raises(InvalidWorkload):
            AnalysisRequest(**fields)
    clash = WorkloadProfile(
        "P", WorkloadType.REQUEST_RESPONSE, peak_rate=rps("1"), assumptions=(WorkloadAssumption("x_y", "a"),)
    )
    with pytest.raises(InvalidWorkload):
        AnalysisRequest(uuid.UUID(int=1), 1, clash, assumptions=(WorkloadAssumption("x_y", "b"),))


def analysis() -> CapacityAnalysis:
    return CapacityAnalysis(uuid.uuid7(), uuid.uuid7(), uuid.uuid7(), 1, "a" * 64, "pending", None, NOW)


def test_the_lifecycle_ends_in_what_the_result_established() -> None:
    running = analysis().start(NOW)
    done = running.finish(full_result(), NOW)
    assert (done.status, done.finished, done.summary is not None) == ("partial", True, True)
    failed = running.fail(AnalysisError("engine_error", "The analysis could not run."), NOW)
    assert (failed.status, failed.result) == ("failed", None)
    moves: tuple[Callable[[], object], ...] = (
        lambda: done.start(NOW),
        lambda: failed.finish(full_result(), NOW),
        lambda: analysis().finish(full_result(), NOW),
    )
    for move in moves:
        with pytest.raises(InvalidAnalysisTransition):
            move()
    assert analysis().fail(AnalysisError("x", "y"), NOW).status == "failed"  # pending may fail directly
