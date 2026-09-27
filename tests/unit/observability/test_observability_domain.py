"""The observability domain contract (Milestone 11, phase 1): IR observability properties, facts
with provenance, coverage states, findings, checks, results and the analysis lifecycle."""

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
from core.domain.capacity.results import Certainty, Source
from core.domain.engine_results import Evidence, FindingBasis, ModelSet, Unsupported
from core.domain.observability.analyses import (
    ObservabilityAnalysis,
    ObservabilityAnalysisError,
    ObservabilityAnalysisRequest,
    ObservabilityAssumption,
)
from core.domain.observability.errors import (
    InvalidObservabilityAnalysisTransition,
    InvalidObservabilityRequest,
    InvalidObservabilityResult,
)
from core.domain.observability.inputs import ComponentObservability, ConnectionObservability
from core.domain.observability.results import (
    FINDING_ID,
    TYPES,
    CheckResult,
    CheckSource,
    ComponentResult,
    Condition,
    FindingCategory,
    FindingType,
    ObservabilityFinding,
    ObservabilityResult,
    ObservabilityStatus,
    coverage_counts,
)
from core.domain.observability.values import CoverageState, Dimension
from core.domain.redaction import REDACTED
from core.domain.validation.results import Severity, Verdict
from tests.unit.architecture_ir.builders import connection, node

AT = datetime(2026, 9, 27, tzinfo=UTC)
ANALYZERS = ModelSet.of([("logs", 1)])
S = CoverageState


def finding(**overrides: Any) -> ObservabilityFinding:
    fields: dict[str, Any] = {
        "type": FindingType.LOGS_ABSENT,
        "severity": Severity.HIGH,
        "certainty": Certainty.MODELED,
        "title": "api emits no logs",
        "explanation": "api is declared critical and declares logs false.",
        "recommendation": "Review whether api should emit logs.",
        "node_ids": ("api",),
        "evidence": (Evidence("api.configuration.logs", "false"),),
        "analyzer_id": "logs",
        "analyzer_version": 1,
        "dimension": Dimension.LOGGING,
    }
    return ObservabilityFinding(**(fields | overrides))


def coverage(**states: CoverageState) -> dict[Dimension, CoverageState]:
    return {d: states.get(d.value, S.UNKNOWN) for d in Dimension}


def component(node_id: str, criticality: str | None = None, **states: CoverageState) -> ComponentResult:
    return ComponentResult(node_id, criticality, coverage(**states))


# --- IR observability properties -------------------------------------------------------------------


def test_observability_properties_are_declared_on_the_architecture() -> None:
    api: dict[str, Any] = {
        "criticality": "critical",
        "logs": True,
        "structured_logs": True,
        "correlation_ids": True,
        "metrics": ("errors", "latency"),
        "traces": True,
        "trace_context": "propagate",
        "trace_sampling_ratio": Decimal("0.1"),
        "health_check": True,
        "alerts": ("errors", "health"),
        "owner": "payments-oncall",
    }
    monitoring: dict[str, Any] = {"alert_delivery": "paging", "retention_seconds": 2_592_000}
    ArchitectureIR(
        "Shop",
        nodes=(
            node("api", configuration=Configuration(api)),
            node("obs", NodeKind.OBSERVABILITY, configuration=Configuration(monitoring)),
            node("lb", NodeKind.LOAD_BALANCER),
        ),
        connections=(
            connection(
                "api-obs", "api", "obs", configuration=Configuration({"telemetry": ("logs", "metrics")})
            ),
            connection(
                "lb-api",
                "lb",
                "api",
                configuration=Configuration({"health_check": True, "trace_propagation": True}),
            ),
        ),
    )


@pytest.mark.parametrize(
    "values",
    [
        {"criticality": "very"},
        {"metrics": ("errors", "vibes")},
        {"alerts": ("cpu",)},
        {"trace_sampling_ratio": Decimal("1.5")},
        {"trace_context": "drop"},
        {"owner": "Not An Owner"},
        {"logs": "yes"},
    ],
)
def test_invalid_observability_properties_are_refused(values: dict[str, Any]) -> None:
    with pytest.raises(InvalidArchitecture):
        ArchitectureIR("Shop", nodes=(node("api", configuration=Configuration(values)),))


def test_observability_properties_apply_only_where_they_mean_something() -> None:
    with pytest.raises(InvalidArchitecture):  # alert delivery belongs to an observability system
        ArchitectureIR("Shop", nodes=(node("api", configuration=Configuration({"alert_delivery": "email"})),))
    with pytest.raises(InvalidArchitecture):  # a third party's logs are not ours to model
        ArchitectureIR(
            "Shop", nodes=(node("psp", NodeKind.EXTERNAL, configuration=Configuration({"logs": True})),)
        )
    with pytest.raises(InvalidArchitecture):  # a client is not deployed by us
        ArchitectureIR(
            "Shop",
            nodes=(node("web", NodeKind.CLIENT, configuration=Configuration({"criticality": "critical"})),),
        )
    ArchitectureIR(  # but how critical a third party is, and who owns the relationship, can be said
        "Shop",
        nodes=(
            node(
                "psp",
                NodeKind.EXTERNAL,
                configuration=Configuration({"criticality": "critical", "owner": "payments"}),
            ),
        ),
    )


# --- facts ---------------------------------------------------------------------------------------


def test_facts_keep_provenance_and_absence_is_not_a_fact() -> None:
    proposed = Provenance(ProvenanceSource.LLM_PROPOSAL, confidence=Decimal("0.5"))
    api = node(
        "api",
        configuration=Configuration({"criticality": "critical", "metrics": ()}, unknown={"logs"}),
        field_provenance={"configuration.criticality": proposed},
    )
    facts = ComponentObservability.of(api)
    assert facts.critical is True
    assert facts.facts["criticality"].proposed
    assert facts.metric_kinds == ()  # declared none: not the same as not modeled
    assert facts.facts["logs"].source is Source.UNKNOWN
    assert facts.known("logs") is None
    assert "traces" not in facts.facts  # absent: not modeled, never assumed either way
    assert ComponentObservability.of(node("x")).critical is None
    assert ComponentObservability.of(node("x")).metric_kinds is None
    link = ConnectionObservability.of(connection(configuration=Configuration({"telemetry": ("traces",)})))
    assert link.signals == ("traces",)
    assert ConnectionObservability.of(connection()).signals is None


# --- findings ------------------------------------------------------------------------------------


def test_every_type_has_a_fixed_category_and_basis() -> None:
    assert set(TYPES) == set(FindingType)
    assert {basis for _, basis in TYPES.values()} == set(FindingBasis)
    gap = finding()
    assert (gap.category, gap.basis) == (FindingCategory.LOGGING, FindingBasis.CONTROL_GAP)
    assert finding(type=FindingType.LOGS_NOT_MODELED).basis is FindingBasis.NOT_EVALUABLE


def test_finding_ids_are_stable_and_include_dimension_and_check() -> None:
    assert FINDING_ID.fullmatch(finding().id)
    assert finding().id == finding(title="other", severity=Severity.LOW).id
    logs = finding(type=FindingType.TELEMETRY_NOT_COLLECTED, dimension=Dimension.LOGGING)
    metrics = finding(type=FindingType.TELEMETRY_NOT_COLLECTED, dimension=Dimension.METRICS)
    assert logs.id != metrics.id  # one per signal
    a = finding(type=FindingType.REQUIREMENT_VIOLATED, requirement_id="r1", check_key="requirement.req-1.a")
    b = finding(type=FindingType.REQUIREMENT_VIOLATED, requirement_id="r1", check_key="requirement.req-1.b")
    assert a.id != b.id


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"node_ids": ()}, "node_ids"),
        ({"dimension": "logs"}, "dimension"),
        ({"type": FindingType.POLICY_VIOLATED}, "policy_rule"),
        ({"type": FindingType.REQUIREMENT_VIOLATED, "requirement_id": "r"}, "check_key"),
        ({"check_key": "policy.x"}, "check_key"),
        ({"analyzer_version": None}, "analyzer_version"),
    ],
)
def test_invalid_findings_are_refused(overrides: dict[str, Any], field: str) -> None:
    with pytest.raises(InvalidObservabilityResult) as error:
        finding(**overrides)
    assert field in error.value.details["fields"]


def test_a_finding_or_check_that_would_show_a_secret_is_refused() -> None:
    leak = (Evidence("api.configuration.extra.collector_token", "t0k3n"),)
    with pytest.raises(InvalidObservabilityResult):
        finding(evidence=leak)
    with pytest.raises(InvalidObservabilityResult):
        CheckResult(
            "policy.x",
            CheckSource.POLICY,
            Condition.OWNERSHIP,
            Verdict.VIOLATED,
            "No.",
            actual=leak,
            policy_rule="x",
        )
    finding(evidence=(Evidence("api.configuration.extra.collector_token", REDACTED),))


def test_findings_round_trip_and_carry_no_score() -> None:
    original = finding(missing=("api.configuration.logs",), assumptions=("Declared, not verified.",))
    data = original.to_dict()
    assert ObservabilityFinding.from_dict(data) == original
    assert {"score", "maturity", "percentage", "coverage_percent"}.isdisjoint(data)


# --- checks --------------------------------------------------------------------------------------


def test_missing_evidence_or_an_unsupported_condition_is_never_success() -> None:
    def check(**overrides: Any) -> CheckResult:
        fields: dict[str, Any] = {
            "key": "requirement.req-1",
            "source": CheckSource.REQUIREMENT,
            "condition": Condition.OBJECTIVE_MEASURABLE,
            "verdict": Verdict.SATISFIED,
            "explanation": "The latency objective is measured.",
            "requirement_id": "r1",
        }
        return CheckResult(**(fields | overrides))

    assert check().verdict is Verdict.SATISFIED
    with pytest.raises(InvalidObservabilityResult):
        check(missing=("api.configuration.metrics",))
    with pytest.raises(InvalidObservabilityResult):
        check(condition=Condition.UNSUPPORTED)
    unsupported = check(condition=Condition.UNSUPPORTED, verdict=Verdict.NOT_VERIFIABLE)
    assert CheckResult.from_dict(unsupported.to_dict()) == unsupported
    with pytest.raises(InvalidObservabilityResult):  # another engine's condition is not ours
        check(condition=FindingType.LOGS_ABSENT)


# --- coverage and results ------------------------------------------------------------------------


def test_a_component_has_one_explicit_state_per_dimension() -> None:
    result = component("api", "critical", logging=S.MODELED, metrics=S.PARTIAL, tracing=S.ABSENT)
    assert result.coverage[Dimension.HEALTH_CHECKS] is S.UNKNOWN
    assert ComponentResult.from_dict(result.to_dict()) == result
    with pytest.raises(InvalidObservabilityResult):
        ComponentResult("api", None, {Dimension.LOGGING: S.MODELED})  # every dimension, explicitly
    with pytest.raises(InvalidObservabilityResult):
        ComponentResult("api", "vital", coverage())


def test_the_summary_is_reproducible_from_the_components() -> None:
    components = (
        component("api", "critical", logging=S.MODELED, metrics=S.PARTIAL),
        component("jobs", "standard", logging=S.ABSENT),
        component("psp", None, logging=S.UNSUPPORTED),
    )
    result = ObservabilityResult(ANALYZERS, "f" * 64, components)
    summary = result.summary()
    assert summary["coverage"] == coverage_counts(result.components)
    assert summary["coverage"]["logging"] == {
        "modeled": 1,
        "partial": 0,
        "absent": 1,
        "unknown": 0,
        "unsupported": 1,
    }
    assert summary["critical_coverage"]["metrics"]["partial"] == 1
    assert summary["criticality"] == {"critical": 1, "standard": 1, "not_modeled": 1}
    assert not {"score", "percentage", "maturity"} & set(summary)


@pytest.mark.parametrize(
    ("components", "unsupported", "status"),
    [
        ((), (), ObservabilityStatus.UNSUPPORTED),
        ((component("a"),), (), ObservabilityStatus.INSUFFICIENT_INPUT),
        ((component("a", logging=S.MODELED),), (), ObservabilityStatus.PARTIAL),
        (
            (ComponentResult("a", None, dict.fromkeys(Dimension, S.MODELED)),),
            (),
            ObservabilityStatus.COMPLETED,
        ),
        (
            (ComponentResult("a", None, dict.fromkeys(Dimension, S.ABSENT)),),
            (Unsupported("architecture", "analyzer_failed", "x"),),
            ObservabilityStatus.PARTIAL,
        ),
    ],
)
def test_the_status_says_what_was_declared(
    components: tuple[ComponentResult, ...], unsupported: tuple[Unsupported, ...], status: ObservabilityStatus
) -> None:
    assert ObservabilityResult(ANALYZERS, "f" * 64, components, unsupported=unsupported).status is status


def test_results_are_ordered_deduplicated_fingerprinted_and_round_trip() -> None:
    low = finding(type=FindingType.LOGS_NOT_MODELED, severity=Severity.LOW, node_ids=("db",))
    high, duplicate = finding(), finding(title="Same identity")
    result = ObservabilityResult(
        ANALYZERS, "f" * 64, (component("db"), component("api")), (low, duplicate, high)
    )
    assert [f.severity for f in result.findings] == [Severity.HIGH, Severity.LOW]
    again = ObservabilityResult(
        ANALYZERS, "f" * 64, (component("api"), component("db")), (high, low, duplicate)
    )
    assert again.fingerprint == result.fingerprint
    assert ObservabilityResult.from_dict(result.to_dict()) == result


# --- request and lifecycle -----------------------------------------------------------------------


def test_the_request_is_canonical_and_bounded() -> None:
    first, second = uuid.UUID(int=2), uuid.UUID(int=1)
    request = ObservabilityAnalysisRequest(
        uuid.UUID(int=9),
        1,
        scope=("db", "api"),
        requirement_ids=(first, second, first),
        assumptions=(ObservabilityAssumption("agent", "An agent collects host metrics."),),
    )
    assert request.requirement_ids == (second, first)
    assert request.inputs()["requirement_ids"] == [str(second), str(first)]
    fields: dict[str, Any] = {"architecture_id": uuid.UUID(int=9), "revision_number": 1}
    for overrides, reason in (
        ({"requirement_ids": ()}, "empty"),
        ({"requirement_ids": ("r1",)}, "invalid_reference"),
        ({"analyzers": ("Bad",)}, "invalid_reference"),
        ({"scope": tuple(f"n{i}" for i in range(201))}, "too_many"),
    ):
        with pytest.raises(InvalidObservabilityRequest) as error:
            ObservabilityAnalysisRequest(**(fields | overrides))
        assert error.value.details["reason"] == reason


def test_the_lifecycle_ends_in_what_the_result_established() -> None:
    analysis = ObservabilityAnalysis(
        uuid.UUID(int=9), uuid.UUID(int=8), uuid.UUID(int=1), 1, "c" * 64, "pending", None, AT
    )
    running = analysis.start(AT)
    done = running.finish(ObservabilityResult(ANALYZERS, "f" * 64, (component("a"),)), AT)
    assert (done.status, done.finished) == ("insufficient_input", True)
    with pytest.raises(InvalidObservabilityAnalysisTransition):
        done.start(AT)
    failed = running.fail(
        ObservabilityAnalysisError("engine_error", "The analysis could not be completed."), AT
    )
    assert failed.status == "failed"
