"""The reliability model contract, registry and orchestrator (Milestone 9, phase 2), with stand-in
models and steps: a declared value, a two-input formula, a model building on another, broken ones."""

import dataclasses
import logging
import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.results import Certainty, ComponentStatus, Estimate, Source
from core.domain.capacity.units import Quantity
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.reliability.results import FindingType, PathResult, ReliabilityFinding, ReliabilityStatus
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.reliability.context import ReliabilityContext
from engines.reliability.engine import (
    DuplicateModel,
    ModelInputs,
    ModelMeta,
    ModelOutput,
    Progress,
    Registry,
    StepMeta,
    StepOutput,
    analyze,
)
from tests.unit.architecture_ir.builders import node

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
RUNNING = frozenset({NodeKind.SERVICE, NodeKind.DATABASE})


def meta(model_id: str, resources: tuple[str, ...], configuration: tuple[str, ...] = ()) -> ModelMeta:
    return ModelMeta(model_id, 1, model_id, "Stand-in.", RUNNING, resources, configuration, formula="x")


def known(node_id: str, resource: str, value: str, model: ModelMeta) -> Estimate:
    return Estimate(
        node_id, resource, Quantity.of(value, "ratio"), Source.MODEL_ESTIMATE, model.formula, model.id, 1
    )


class Declared:
    meta = meta("declared", ("availability",), ("availability",))

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        value = inputs.facts.number("availability")
        return ModelOutput(
            (
                Estimate(
                    inputs.node.id,
                    "availability",
                    Quantity.of(value, "ratio"),
                    Source.DECLARED,
                    "configuration.availability",
                ),
            )
        )


class Replica:
    meta = meta("replica", ("replica_availability",), ("mtbf_seconds", "mttr_seconds"))

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        mtbf, mttr = inputs.facts.number("mtbf_seconds"), inputs.facts.number("mttr_seconds")
        assert mtbf is not None
        assert mttr is not None
        return ModelOutput(
            (known(inputs.node.id, "replica_availability", str(mtbf / (mtbf + mttr)), self.meta),)
        )


class Combined:
    """Builds on the replica's availability, which an earlier model estimates."""

    meta = meta("combined", ("availability",), ("replicas",))

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        replica = inputs.estimate("replica_availability")
        if replica is None or replica.quantity is None:
            return ModelOutput(
                (
                    Estimate(
                        inputs.node.id,
                        "availability",
                        None,
                        Source.UNKNOWN,
                        "combined",
                        missing=("replica_availability",),
                    ),
                )
            )
        return ModelOutput((known(inputs.node.id, "availability", str(replica.quantity.value), self.meta),))


class Crashing:
    meta = meta("crashing", ("recovery_time",))

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        raise RuntimeError("model bug with internals")


class Malformed:
    meta = meta("malformed", ("recovery_time",))

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        return ModelOutput((known("someone-else", "recovery_time", "1", self.meta),))


class Paths:
    meta = StepMeta("paths", 1, "Paths", "Stand-in.", ("paths",))

    def run(self, context: ReliabilityContext, progress: Progress) -> StepOutput:
        component = progress.component("api")
        estimate = component.estimate("availability") if component else None
        assert estimate is not None
        return StepOutput(
            paths=(
                PathResult(
                    "web", ("web", "api"), ("web-api",), dataclasses.replace(estimate, element_id="web")
                ),
            )
        )


class Findings:
    """Sees what the steps before it produced."""

    meta = StepMeta("findings", 1, "Findings", "Stand-in.", ("findings",))

    def run(self, context: ReliabilityContext, progress: Progress) -> StepOutput:
        assert progress.paths, "the paths step ran first"
        finding = ReliabilityFinding(
            FindingType.SINGLE_POINT_OF_FAILURE, Severity.HIGH, Certainty.CANDIDATE, "api alone",
            "Every path needs api.", "Consider a second replica.", node_ids=("api",),
        )  # fmt: skip
        return StepOutput(findings=(finding,))


class Overreaching:
    meta = StepMeta("overreaching", 1, "Overreaching", "Declares paths, returns findings.", ("paths",))

    def run(self, context: ReliabilityContext, progress: Progress) -> StepOutput:
        return Findings().run(context, dataclasses.replace(progress, paths=(None,)))  # type: ignore[arg-type]


class CrashingStep:
    meta = StepMeta("crashing-step", 1, "Crashing", "Stand-in.", ("findings",))

    def run(self, context: ReliabilityContext, progress: Progress) -> StepOutput:
        raise RuntimeError("step bug")


def ir(*nodes: Any) -> ArchitectureIR:
    return ArchitectureIR("Shop", nodes=(node("web", NodeKind.CLIENT), *nodes))


def run(architecture: ArchitectureIR, *models: Any, steps: tuple[Any, ...] = ()) -> Any:
    request = ReliabilityAnalysisRequest(uuid.UUID(int=1), 1)
    return analyze(ReliabilityContext(architecture, REVISION, request), Registry(models, steps))


def api(**values: Any) -> Any:
    return node("api", configuration=Configuration(values))


# --- registry ------------------------------------------------------------------------------------


def test_the_registry_refuses_duplicates_and_unknown_resources_and_keeps_its_order() -> None:
    with pytest.raises(DuplicateModel):
        Registry([Declared(), Declared()])
    with pytest.raises(DuplicateModel):
        Registry([Declared()], [Paths(), Paths()])

    class Odd:
        meta = meta("odd", ("uptime_score",))

        def estimate(self, inputs: ModelInputs) -> ModelOutput:
            return ModelOutput()

    with pytest.raises(DuplicateModel):
        Registry([Odd()])
    registry = Registry([Replica(), Combined(), Declared()], [Paths()])
    assert [m.meta.id for m in registry.models()] == ["replica", "combined", "declared"]
    assert registry.model_set().models == (("combined", 1), ("declared", 1), ("paths", 1), ("replica", 1))
    assert set(Declared.meta.to_dict()) >= {
        "formula",
        "assumptions",
        "unsupported",
        "limitations",
        "configuration",
    }


# --- models --------------------------------------------------------------------------------------


def test_the_first_model_to_establish_a_value_takes_precedence() -> None:
    result = run(
        ir(api(availability=Decimal("0.999"), replicas=2, mtbf_seconds=99, mttr_seconds=1)),
        Declared(),
        Replica(),
        Combined(),
    )
    [component] = result.components
    assert component.estimate("availability").source is Source.DECLARED  # declared before combined
    assert component.estimate("replica_availability").quantity == Quantity.of("0.99", "ratio")
    assert component.models == (("declared", 1), ("replica", 1))  # combined was not needed


def test_a_later_model_builds_on_an_earlier_estimate() -> None:
    result = run(ir(api(replicas=2, mtbf_seconds=99, mttr_seconds=1)), Replica(), Combined())
    [component] = result.components
    assert component.estimate("availability").quantity == Quantity.of("0.99", "ratio")
    assert component.status is ComponentStatus.ESTIMATED
    assert component.missing == ()


def test_missing_inputs_are_named_only_for_what_stays_unknown() -> None:
    result = run(ir(api(mtbf_seconds=99), node("db", NodeKind.DATABASE)), Declared(), Replica(), Combined())
    api_result, db = result.components
    assert api_result.status is ComponentStatus.INSUFFICIENT_INPUT
    assert api_result.missing == (
        "configuration.availability",
        "configuration.mttr_seconds",
        "configuration.replicas",
    )
    assert db.missing == (
        "configuration.availability",
        "configuration.mtbf_seconds",
        "configuration.mttr_seconds",
        "configuration.replicas",
    )
    assert result.status is ReliabilityStatus.INSUFFICIENT_INPUT
    partial = run(
        ir(api(availability=Decimal("0.999")), node("db", NodeKind.DATABASE)), Declared(), Replica()
    )
    assert partial.status is ReliabilityStatus.PARTIAL
    # api's availability is known; its replicas' is not, and says what it lacks
    assert partial.components[0].status is ComponentStatus.ESTIMATED
    assert partial.components[0].missing == ("configuration.mtbf_seconds", "configuration.mttr_seconds")


def test_an_unknown_value_is_as_missing_as_an_absent_one() -> None:
    unknown = node("api", configuration=Configuration({"mtbf_seconds": 99}, unknown={"mttr_seconds"}))
    [component] = run(ir(unknown), Replica()).components
    assert component.missing == ("configuration.mttr_seconds",)


def test_components_no_model_supports_are_reported_and_clients_are_out_of_scope() -> None:
    result = run(ir(node("q", NodeKind.QUEUE), node("zone", NodeKind.BOUNDARY)), Declared())
    assert [(c.node_id, c.status) for c in result.components] == [("q", ComponentStatus.UNSUPPORTED)]
    assert [(u.element_id, u.code) for u in result.unsupported] == [("q", "no_reliability_model")]
    assert result.status is ReliabilityStatus.UNSUPPORTED
    empty = run(ArchitectureIR("Empty", nodes=(node("web", NodeKind.CLIENT),)), Declared())
    assert "no_components" in [x.code for x in empty.limitations]


def test_failing_and_malformed_models_are_isolated(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, "architectos.reliability"):
        result = run(ir(api(availability=Decimal("0.999"))), Declared(), Crashing(), Malformed())
    [component] = result.components
    assert component.status is ComponentStatus.ESTIMATED  # the declared value still counts
    assert [(u.element_id, u.code) for u in result.unsupported] == [
        ("api", "invalid_output"),
        ("api", "model_failed"),
    ]
    assert "internals" not in str(result.to_dict())
    assert "reliability model failed" in caplog.text


# --- steps ---------------------------------------------------------------------------------------


def test_steps_run_in_order_and_see_what_came_before() -> None:
    result = run(ir(api(availability=Decimal("0.999"))), Declared(), steps=(Paths(), Findings()))
    assert [p.entry_id for p in result.paths] == ["web"]
    assert [f.type for f in result.findings] == [FindingType.SINGLE_POINT_OF_FAILURE]
    assert result.status is ReliabilityStatus.COMPLETED


def test_failing_and_overreaching_steps_are_isolated(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, "architectos.reliability"):
        result = run(
            ir(api(availability=Decimal("0.999"))),
            Declared(),
            steps=(Paths(), CrashingStep(), Overreaching()),
        )
    assert [(u.element_id, u.code) for u in result.unsupported] == [
        ("architecture", "invalid_output"), ("architecture", "step_failed"),
    ]  # fmt: skip
    assert len(result.paths) == 1  # the paths step still counts
    assert result.findings == ()


# --- determinism ---------------------------------------------------------------------------------


def test_the_same_inputs_give_the_same_result_whatever_the_node_order() -> None:
    nodes = (
        api(availability=Decimal("0.999")),
        node("db", NodeKind.DATABASE, configuration=Configuration({"mtbf_seconds": 9, "mttr_seconds": 1})),
    )
    first = run(ir(*nodes), Declared(), Replica(), steps=(Paths(), Findings()))
    second = run(ir(*reversed(nodes)), Declared(), Replica(), steps=(Paths(), Findings()))
    assert first.to_dict() == second.to_dict()
    assert first.fingerprint == second.fingerprint
    assert {x.code for x in first.limitations} == {"not_measured", "no_catalog"}
