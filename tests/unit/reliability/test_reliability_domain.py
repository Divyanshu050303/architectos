"""The reliability domain contract (Milestone 9, phase 1): units, inputs with provenance, findings,
objectives, results and the analysis lifecycle."""

import dataclasses
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.domain.capacity.results import Certainty, ComponentStatus, Estimate, Source
from core.domain.capacity.units import Quantity
from core.domain.engine_results import Evidence, ModelSet, Unsupported
from core.domain.reliability.analyses import (
    Objective,
    ReliabilityAnalysis,
    ReliabilityAnalysisError,
    ReliabilityAnalysisRequest,
    ReliabilityAssumption,
)
from core.domain.reliability.errors import (
    InvalidReliabilityAnalysisTransition,
    InvalidReliabilityRequest,
    InvalidReliabilityResult,
)
from core.domain.reliability.inputs import ComponentReliability
from core.domain.reliability.results import (
    ComponentResult,
    FindingType,
    ObjectiveKind,
    ObjectiveResult,
    PathResult,
    ReliabilityFinding,
    ReliabilityResult,
    ReliabilityStatus,
)
from core.domain.reliability.values import availability, downtime_minutes_per_month, fraction, in_seconds
from core.domain.validation.results import Severity, Verdict
from tests.unit.architecture_ir.builders import node

AT = datetime(2026, 9, 26, tzinfo=UTC)
MODELS = ModelSet.of([("mtbf-mttr", 1)])


# --- values --------------------------------------------------------------------------------------


def test_availability_is_an_exact_fraction_kept_to_nine_places() -> None:
    assert fraction("0.9995", "a") == Decimal("0.9995")
    assert fraction(1, "a") == Decimal(1)
    for raw, reason in (("1.01", "out_of_range"), ("-0.1", "negative"), ("x", "not_a_number")):
        with pytest.raises(InvalidReliabilityRequest) as error:
            fraction(raw, "a")
        assert error.value.details["reason"] == reason
    assert availability(Decimal("0.99999999999")) == Quantity.of("1", "ratio")  # 9 places, half-even
    assert availability(Decimal("0.9999999994")) == Quantity.of("0.999999999", "ratio")


def test_durations_have_explicit_units() -> None:
    assert in_seconds(Quantity.of("15", "min"), "rto") == Decimal(900)
    assert in_seconds(Quantity.of("250", "ms"), "rto") == Decimal("0.25")
    with pytest.raises(InvalidReliabilityRequest):
        in_seconds(Quantity.of("5", "requests/second"), "rto")


def test_downtime_is_what_an_availability_allows() -> None:
    assert downtime_minutes_per_month(Decimal("0.999")) == Decimal("43.8")  # 730 h x 60 x 0.001
    assert downtime_minutes_per_month(Decimal(1)) == 0


# --- inputs --------------------------------------------------------------------------------------


def test_component_facts_keep_their_source_and_provenance() -> None:
    discovered = Provenance(ProvenanceSource.TERRAFORM, "aws_db_instance.main")
    db = node(
        "db", NodeKind.DATABASE,
        configuration=Configuration(
            {"replicas": 2, "replica_availability": Decimal("0.995"), "failure_independence": "independent"},
            unknown={"mttr_seconds"},
        ),
        field_provenance={"configuration.replicas": discovered},
    )  # fmt: skip
    facts = ComponentReliability.of(db)
    assert facts.number("replicas") == 2
    assert facts.facts["replicas"].provenance == "terraform"
    assert facts.facts["mttr_seconds"].source is Source.UNKNOWN
    assert facts.known("mttr_seconds") is None  # unknown is never 0
    assert "mtbf_seconds" not in facts.facts  # absent is not a fact
    assert facts.missing(["mtbf_seconds", "mttr_seconds", "replicas"]) == (
        "configuration.mtbf_seconds", "configuration.mttr_seconds",
    )  # fmt: skip
    assert facts.evidence(["replicas", "mttr_seconds"]) == (
        Evidence("configuration.replicas", "2 (terraform)"),
        Evidence("configuration.mttr_seconds", "unknown"),
    )


@pytest.mark.parametrize(
    "values",
    [
        {"availability": Decimal("1.5")},
        {"replica_availability": Decimal("-0.1")},
        {"mttr_seconds": Decimal("-1")},
        {"min_healthy_replicas": 0},
        {"failure_independence": "sometimes"},
        {"failover_mode": "magic"},
        {"redundancy_group": "Not A Group"},
    ],
)
def test_invalid_reliability_properties_are_refused_by_the_architecture(values: dict[str, Any]) -> None:
    with pytest.raises(InvalidArchitecture):
        ArchitectureIR("Shop", nodes=(node("api", configuration=Configuration(values)),))


def test_reliability_properties_apply_only_where_they_mean_something() -> None:
    with pytest.raises(InvalidArchitecture):  # a client has no replicas to repair
        ArchitectureIR(
            "Shop", nodes=(node("web", NodeKind.CLIENT, configuration=Configuration({"mttr_seconds": 5})),)
        )
    with pytest.raises(InvalidArchitecture):  # backups are for data stores
        ArchitectureIR(
            "Shop", nodes=(node("api", configuration=Configuration({"backup_interval_seconds": 5})),)
        )
    ArchitectureIR(
        "Shop",
        nodes=(
            node("idp", NodeKind.EXTERNAL, configuration=Configuration({"availability": Decimal("0.999")})),
        ),
    )


# --- findings and objectives ---------------------------------------------------------------------


def finding(**changes: Any) -> ReliabilityFinding:
    fields: dict[str, Any] = {
        "type": FindingType.SINGLE_POINT_OF_FAILURE,
        "severity": Severity.HIGH,
        "certainty": Certainty.MODELED,
        "title": "db is a single point of failure",
        "explanation": "Every request path needs db, and db has one replica.",
        "recommendation": "Consider a replica in another zone, then review failover.",
        "node_ids": ("db",),
        "evidence": (Evidence("configuration.replicas", "1"),),
    }
    return ReliabilityFinding(**(fields | changes))


def test_finding_ids_are_stable_and_ordering_is_by_severity() -> None:
    first = finding(node_ids=("db", "api"))
    assert first.id == finding(node_ids=("api", "db"), title="other words").id
    assert first.id.startswith("rel_")
    assert first.id != finding(type=FindingType.NO_REDUNDANCY, node_ids=("db", "api")).id
    low = finding(severity=Severity.LOW, node_ids=("cache",))
    assert sorted([low, first], key=ReliabilityFinding.sort_key) == [first, low]
    assert ReliabilityFinding.from_dict(first.to_dict()) == first


def test_a_finding_names_what_it_is_about() -> None:
    with pytest.raises(InvalidReliabilityResult):
        finding(node_ids=())
    with pytest.raises(InvalidReliabilityResult):
        finding(model_id="mtbf-mttr")  # a model without its version


def test_missing_evidence_is_never_success() -> None:
    fields: dict[str, Any] = {
        "key": "availability", "kind": ObjectiveKind.AVAILABILITY, "target": "0.999",
        "explanation": "No path has a known availability.",
        "missing": ("configuration.replica_availability",),
    }  # fmt: skip
    with pytest.raises(InvalidReliabilityResult):
        ObjectiveResult(verdict=Verdict.SATISFIED, **fields)
    result = ObjectiveResult(verdict=Verdict.NOT_VERIFIABLE, **fields)
    assert ObjectiveResult.from_dict(result.to_dict()) == result


# --- results -------------------------------------------------------------------------------------


def estimate(element: str, value: str | None) -> Estimate:
    if value is None:
        return Estimate(
            element,
            "availability",
            None,
            Source.UNKNOWN,
            "mtbf / (mtbf + mttr)",
            missing=("configuration.mttr_seconds",),
        )
    return Estimate(
        element,
        "availability",
        Quantity.of(value, "ratio"),
        Source.MODEL_ESTIMATE,
        "mtbf / (mtbf + mttr)",
        "mtbf-mttr",
        1,
    )


def component(node_id: str, value: str | None) -> ComponentResult:
    status = ComponentStatus.ESTIMATED if value is not None else ComponentStatus.INSUFFICIENT_INPUT
    return ComponentResult(node_id, status, (("mtbf-mttr", 1),), (estimate(node_id, value),))


def result(
    *components: ComponentResult, paths: tuple[PathResult, ...] = (), **kwargs: Any
) -> ReliabilityResult:
    return ReliabilityResult(MODELS, "f" * 64, components, paths, **kwargs)


def test_the_status_says_what_was_established() -> None:
    known, unknown = component("api", "0.999"), component("db", None)
    path = PathResult("web", ("web", "api"), ("web-api",), estimate("web", "0.999"))
    assert result(known, paths=(path,)).status is ReliabilityStatus.COMPLETED
    assert result(known, unknown).status is ReliabilityStatus.PARTIAL
    assert result(unknown).status is ReliabilityStatus.INSUFFICIENT_INPUT
    assert (
        result(unsupported=(Unsupported("q", "no_reliability_model", "x"),)).status
        is ReliabilityStatus.UNSUPPORTED
    )
    assert (
        result(known, unsupported=(Unsupported("q", "no_reliability_model", "x"),)).status
        is ReliabilityStatus.PARTIAL
    )


def test_results_are_ordered_deduplicated_fingerprinted_and_round_trip() -> None:
    findings = (
        finding(node_ids=("db",)),
        finding(node_ids=("db",), title="same finding"),
        finding(severity=Severity.CRITICAL, node_ids=("lb",)),
    )
    first = result(component("db", None), component("api", "0.999"), findings=findings)
    assert [c.node_id for c in first.components] == ["api", "db"]
    assert [f.node_ids for f in first.findings] == [("lb",), ("db",)]  # critical first; duplicates merged
    assert first.summary()["findings"] == {"critical": 1, "high": 1, "medium": 0, "low": 0, "info": 0}
    again = ReliabilityResult.from_dict(first.to_dict())
    assert again.to_dict() == first.to_dict()
    assert again.fingerprint == first.fingerprint
    reordered = result(component("api", "0.999"), component("db", None), findings=tuple(reversed(findings)))
    assert reordered.fingerprint == first.fingerprint
    with pytest.raises(InvalidReliabilityResult):
        result(component("api", "0.9"), component("api", "0.8"))


# --- the request ---------------------------------------------------------------------------------


def request(**changes: Any) -> ReliabilityAnalysisRequest:
    return ReliabilityAnalysisRequest(uuid.UUID(int=1), 3, **changes)


def test_objectives_are_typed_and_unit_aware() -> None:
    rto = Objective(
        "rto", ObjectiveKind.RECOVERY_TIME, duration=Quantity.of("15", "min"), node_ids=("db", "api", "db")
    )
    assert (rto.seconds, rto.node_ids, rto.stated) == (Decimal(900), ("api", "db"), "<= 15 min")
    target = Objective("slo", ObjectiveKind.AVAILABILITY, target=Decimal("0.999"))
    assert target.stated == ">= 0.999"
    assert (target.met(Decimal("0.999")), target.met(Decimal("0.9989"))) == (True, False)
    strict = Objective("rto2", ObjectiveKind.RECOVERY_TIME, duration=Quantity.of("1", "min"), strict=True)
    assert (strict.stated, strict.met(Decimal(60)), strict.met(Decimal(59))) == ("< 1 min", False, True)
    assert Objective("n", ObjectiveKind.REDUNDANCY, target=Decimal("2.0")).target == Decimal(2)


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"kind": ObjectiveKind.AVAILABILITY, "target": Decimal("1.2")}, "objectives.target"),
        ({"kind": ObjectiveKind.AVAILABILITY}, "objectives.target"),
        ({"kind": ObjectiveKind.RECOVERY_TIME, "target": Decimal(5)}, "objectives.duration"),
        ({"kind": ObjectiveKind.DATA_LOSS, "duration": Quantity.of("5", "B")}, "objectives.duration"),
        (
            {"kind": ObjectiveKind.AVAILABILITY, "target": Decimal("0.9"), "duration": Quantity.of("1", "s")},
            "objectives.duration",
        ),
        ({"kind": ObjectiveKind.REDUNDANCY, "target": Decimal("1.5")}, "objectives.target"),
        ({"kind": ObjectiveKind.REDUNDANCY, "target": 0}, "objectives.target"),
        ({"kind": ObjectiveKind.REDUNDANCY, "target": 2, "node_ids": ("",)}, "objectives.node_ids"),
    ],
)
def test_invalid_objectives_are_refused(fields: dict[str, Any], field: str) -> None:
    with pytest.raises(InvalidReliabilityRequest) as error:
        Objective("o", **fields)
    assert error.value.details["field"] == field


def test_the_request_is_canonical_and_bounded() -> None:
    objectives = (
        Objective("rto", ObjectiveKind.RECOVERY_TIME, duration=Quantity.of("1", "h")),
        Objective("availability", ObjectiveKind.AVAILABILITY, target=Decimal("0.999")),
    )
    first = request(
        entries=("web", "mobile"),
        objectives=objectives,
        assumptions=(ReliabilityAssumption("zones", "Zones fail apart."),),
    )
    assert [o["key"] for o in first.inputs()["objectives"]] == ["availability", "rto"]
    assert first.inputs()["entries"] == ["mobile", "web"]
    zones = (ReliabilityAssumption("zones", "Zones fail apart."),)
    same = request(entries=("mobile", "web"), objectives=tuple(reversed(objectives)), assumptions=zones)
    assert first.inputs() == same.inputs()
    for changes, field in (
        ({"entries": ()}, "entries"),
        ({"objectives": objectives + objectives}, "objectives"),
        ({"assumptions": (ReliabilityAssumption("a", "x"), ReliabilityAssumption("a", "y"))}, "assumptions"),
        ({"label": " "}, "label"),
    ):
        with pytest.raises(InvalidReliabilityRequest) as error:
            request(**changes)
        assert error.value.details["field"] == field
    with pytest.raises(InvalidReliabilityRequest):
        ReliabilityAnalysisRequest(uuid.UUID(int=1), 0)


def test_the_lifecycle_ends_in_what_the_result_established() -> None:
    analysis = ReliabilityAnalysis(
        uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3), 1, "c" * 64, "pending", None, AT
    )
    running = analysis.start(AT)
    done = running.finish(result(component("api", None)), AT)
    assert (done.status, done.finished) == ("insufficient_input", True)
    with pytest.raises(InvalidReliabilityAnalysisTransition):
        done.start(AT)
    failed = dataclasses.replace(analysis).fail(
        ReliabilityAnalysisError("engine_error", "It could not run."), AT
    )
    assert failed.status == "failed"
