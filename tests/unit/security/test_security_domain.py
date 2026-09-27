"""The security domain contract (Milestone 10, phase 1): IR security properties, facts with
provenance, sensitivity, redaction, findings, checks, results and the analysis lifecycle."""

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
from core.domain.capacity.results import Certainty, Source
from core.domain.engine_results import Evidence, ModelSet, Unsupported
from core.domain.security.analyses import (
    SecurityAnalysis,
    SecurityAnalysisError,
    SecurityAnalysisRequest,
    SecurityAssumption,
)
from core.domain.security.errors import (
    InvalidSecurityAnalysisTransition,
    InvalidSecurityRequest,
    InvalidSecurityResult,
)
from core.domain.security.inputs import BoundarySecurity, ComponentSecurity, ConnectionSecurity
from core.domain.security.results import (
    FINDING_ID,
    TYPES,
    CheckResult,
    CheckSource,
    ComponentResult,
    Condition,
    Coverage,
    FindingBasis,
    FindingCategory,
    FindingType,
    SecurityFinding,
    SecurityResult,
    SecurityStatus,
    StrideCategory,
    TrustZone,
)
from core.domain.security.values import REDACTED, redacted, sensitivity, shows_a_secret
from core.domain.validation.results import Severity, Verdict
from tests.unit.architecture_ir.builders import connection, node

AT = datetime(2026, 9, 26, tzinfo=UTC)
ANALYZERS = ModelSet.of([("exposure", 1)])


def finding(**overrides: Any) -> SecurityFinding:
    fields: dict[str, Any] = {
        "type": FindingType.MISSING_AUTHENTICATION,
        "severity": Severity.HIGH,
        "certainty": Certainty.MODELED,
        "title": "api accepts unauthenticated requests from the internet",
        "explanation": "It is public and declares authentication none.",
        "recommendation": "Review whether api should require authentication.",
        "node_ids": ("api",),
        "evidence": (Evidence("api.configuration.exposure", "public"),),
        "analyzer_id": "authentication",
        "analyzer_version": 1,
    }
    return SecurityFinding(**(fields | overrides))


def check(**overrides: Any) -> CheckResult:
    fields: dict[str, Any] = {
        "key": "policy.require_encryption_at_rest",
        "source": CheckSource.POLICY,
        "condition": Condition.ENCRYPTION_AT_REST,
        "verdict": Verdict.SATISFIED,
        "explanation": "Every data store declares encryption at rest.",
        "node_ids": ("db",),
        "actual": (Evidence("db.configuration.encryption_at_rest", "true"),),
        "policy_rule": "require_encryption_at_rest",
    }
    return CheckResult(**(fields | overrides))


# --- IR security properties ------------------------------------------------------------------------


def test_security_properties_are_declared_on_the_architecture() -> None:
    ArchitectureIR(
        "Shop",
        nodes=(
            node(
                "zone",
                NodeKind.BOUNDARY,
                configuration=Configuration({"boundary_type": "trust_zone", "trust_level": "internal"}),
            ),
            node(
                "api",
                parent_id="zone",
                configuration=Configuration(
                    {
                        "exposure": "public",
                        "authentication": "oauth2",
                        "authorization": "rbac",
                        "sensitive_operations": True,
                        "management_interface": False,
                        "data_classification": "confidential",
                        "personal_data": True,
                        "secrets_required": True,
                        "secret_source": "secret_manager",
                        "secret_rotation": True,
                        "audit_logging": True,
                    }
                ),
            ),
            node(
                "db",
                NodeKind.DATABASE,
                parent_id="zone",
                configuration=Configuration({"encryption_at_rest": True}),
            ),
        ),
        connections=(
            connection(
                configuration=Configuration(
                    {
                        "tls": True,
                        "authentication": "mtls",
                        "data_classification": "restricted",
                        "personal_data": True,
                    }
                )
            ),
        ),
    )


@pytest.mark.parametrize(
    "values",
    [
        {"exposure": "internet"},
        {"authentication": "magic"},
        {"authorization": "trust_me"},
        {"data_classification": "secret"},
        {"secret_source": "vault"},  # a secret manager is "secret_manager", whatever the product
        {"audit_logging": "yes"},
    ],
)
def test_invalid_security_properties_are_refused_by_the_architecture(values: dict[str, Any]) -> None:
    with pytest.raises(InvalidArchitecture):
        ArchitectureIR("Shop", nodes=(node("api", configuration=Configuration(values)),))


def test_security_properties_apply_only_where_they_mean_something() -> None:
    with pytest.raises(InvalidArchitecture):  # a service stores nothing to encrypt at rest
        ArchitectureIR(
            "Shop", nodes=(node("api", configuration=Configuration({"encryption_at_rest": True})),)
        )
    with pytest.raises(InvalidArchitecture):  # a trust level belongs to a boundary
        ArchitectureIR("Shop", nodes=(node("api", configuration=Configuration({"trust_level": "internal"})),))
    with pytest.raises(InvalidArchitecture):  # a client is not deployed by us
        ArchitectureIR(
            "Shop", nodes=(node("web", NodeKind.CLIENT, configuration=Configuration({"exposure": "public"})),)
        )
    with pytest.raises(InvalidArchitecture):  # a third party's secrets are not ours to source
        ArchitectureIR(
            "Shop",
            nodes=(node("idp", NodeKind.EXTERNAL, configuration=Configuration({"secret_source": "file"})),),
        )
    ArchitectureIR(  # but how it authenticates callers is
        "Shop",
        nodes=(node("idp", NodeKind.EXTERNAL, configuration=Configuration({"authentication": "oauth2"})),),
    )


# --- facts ---------------------------------------------------------------------------------------


def test_facts_keep_their_source_and_provenance_and_absence_is_not_a_fact() -> None:
    proposed = Provenance(ProvenanceSource.LLM_PROPOSAL, confidence=Decimal("0.6"), inferred=True)
    api = node(
        "api",
        configuration=Configuration(
            {"exposure": "public", "authentication": "none"}, unknown={"authorization"}
        ),
        field_provenance={"configuration.authentication": proposed},
    )
    facts = ComponentSecurity.of(api)
    assert facts.element_id == "api"
    assert facts.known("exposure") == "public"
    assert facts.facts["authorization"].source is Source.UNKNOWN
    assert facts.known("authorization") is None  # unknown is never "none"
    assert "audit_logging" not in facts.facts  # absent: not modeled, never assumed either way
    assert facts.facts["authentication"].proposed
    assert facts.evidence(["authentication", "authorization"]) == (
        Evidence("configuration.authentication", "none (llm_proposal, inferred)"),
        Evidence("configuration.authorization", "unknown"),
    )
    assert facts.missing(["exposure", "audit_logging"]) == ("configuration.audit_logging",)


def test_connection_and_boundary_facts() -> None:
    link = ConnectionSecurity.of(
        connection(configuration=Configuration({"tls": False, "personal_data": True}))
    )
    assert (link.element_id, link.known("tls"), link.sensitive) == ("api-db", False, True)
    zone = BoundarySecurity.of(
        node("zone", NodeKind.BOUNDARY, configuration=Configuration({"boundary_type": "trust_zone"}))
    )
    assert (zone.trust_zone, zone.known("trust_level")) == (True, None)
    network = BoundarySecurity.of(
        node("net", NodeKind.BOUNDARY, configuration=Configuration({"boundary_type": "network"}))
    )
    assert not network.trust_zone


@pytest.mark.parametrize(
    ("classification", "personal", "expected"),
    [
        ("restricted", None, True),
        ("confidential", False, True),
        ("internal", True, True),  # personal data is sensitive whatever the classification
        ("public", None, False),
        ("internal", False, False),
        (None, False, None),  # no classification: not established
        (None, None, None),
    ],
)
def test_sensitivity_comes_only_from_declared_data(
    classification: str | None, personal: bool | None, expected: bool | None
) -> None:
    assert sensitivity(classification, personal) is expected


# --- redaction -----------------------------------------------------------------------------------


def test_secret_looking_values_are_never_shown_but_closed_properties_are() -> None:
    assert redacted("api.configuration.extra.db_password", "hunter2") == Evidence(
        "api.configuration.extra.db_password", REDACTED
    )
    assert redacted("api.metadata.api_token", "t") == Evidence("api.metadata.api_token", REDACTED)
    assert redacted("api.configuration.authorization", "rbac").value == "rbac"  # a choice: no secret
    assert redacted("api.configuration.secret_source", "hardcoded").value == "hardcoded"
    assert shows_a_secret([Evidence("configuration.extra.api_key", "abc")])
    assert not shows_a_secret([Evidence("configuration.extra.api_key", REDACTED)])


def test_a_dotted_key_is_a_secret_when_any_part_of_it_looks_like_one() -> None:
    for path in (
        "configuration.extra.password.hash",
        "api.configuration.extra.token.value",
        "api.metadata.api_key.live_value",
    ):
        assert redacted(path, "s3cr3t").value == REDACTED, path
    assert (
        redacted("token-svc.configuration.exposure", "public").value == "public"
    )  # an element id is not a key
    assert shows_a_secret([Evidence("api.configuration.extra.secret.id", "abc")])


def test_a_finding_or_check_that_would_show_a_secret_is_refused() -> None:
    leak = (Evidence("api.configuration.extra.db_password", "hunter2"),)
    with pytest.raises(InvalidSecurityResult) as error:
        finding(type=FindingType.SECRET_IN_CONFIGURATION, evidence=leak)
    assert error.value.details["fields"] == ["evidence"]
    assert "hunter2" not in str(error.value.details)
    with pytest.raises(InvalidSecurityResult):
        check(actual=leak)
    finding(type=FindingType.SECRET_IN_CONFIGURATION, evidence=(redacted(leak[0].label, leak[0].value),))


# --- findings ------------------------------------------------------------------------------------


def test_every_type_has_a_fixed_category_and_basis() -> None:
    assert set(TYPES) == set(FindingType)
    kinds = {basis for _, basis in TYPES.values()}
    assert kinds == set(FindingBasis)  # the four kinds are all represented, never collapsed
    gap = finding()
    assert (gap.category, gap.basis) == (FindingCategory.AUTHENTICATION, FindingBasis.CONTROL_GAP)
    unknown = finding(type=FindingType.AUTHENTICATION_NOT_MODELED)
    assert unknown.basis is FindingBasis.NOT_EVALUABLE
    assert gap.id != unknown.id  # a gap and a missing model of the same element are different findings


def test_finding_ids_are_stable_and_include_threat_requirement_and_policy() -> None:
    assert FINDING_ID.fullmatch(finding().id)
    assert finding().id == finding(title="another title", severity=Severity.LOW).id
    assert finding(node_ids=("api", "db")).id == finding(node_ids=("db", "api", "db")).id
    spoof = finding(type=FindingType.THREAT_CANDIDATE, threat=StrideCategory.SPOOFING)
    tamper = finding(type=FindingType.THREAT_CANDIDATE, threat=StrideCategory.TAMPERING)
    assert spoof.id != tamper.id
    a = finding(type=FindingType.REQUIREMENT_VIOLATED, requirement_id=str(uuid.UUID(int=1)), check_key="r.1")
    b = finding(type=FindingType.REQUIREMENT_VIOLATED, requirement_id=str(uuid.UUID(int=2)), check_key="r.2")
    assert a.id != b.id
    # one requirement checked as two conditions on the same elements: two findings (review finding)
    twice = finding(
        type=FindingType.REQUIREMENT_VIOLATED, requirement_id=str(uuid.UUID(int=1)), check_key="r.1.x"
    )
    assert twice.id != a.id
    policy = finding(
        type=FindingType.POLICY_VIOLATED, policy_rule="require_tls", check_key="policy.require_tls"
    )
    assert (policy.to_dict()["policy_rule"], policy.to_dict()["check_key"]) == (
        "require_tls",
        "policy.require_tls",
    )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"node_ids": ()}, "node_ids"),  # a finding is about something
        ({"title": " "}, "title"),
        ({"analyzer_version": None}, "analyzer_version"),
        ({"type": FindingType.THREAT_CANDIDATE}, "threat"),  # a threat has its STRIDE category
        ({"threat": StrideCategory.SPOOFING}, "threat"),  # and only threats have one
        ({"type": FindingType.REQUIREMENT_VIOLATED}, "requirement_id"),
        ({"type": FindingType.REQUIREMENT_VIOLATED, "requirement_id": "r"}, "check_key"),  # its check
        ({"check_key": "policy.x"}, "check_key"),  # only requirement and policy findings report a check
        ({"requirement_id": "r"}, "requirement_id"),
        ({"type": FindingType.POLICY_NOT_EVALUABLE}, "policy_rule"),
        ({"severity": "urgent"}, "severity"),
    ],
)
def test_invalid_findings_are_refused(overrides: dict[str, Any], field: str) -> None:
    with pytest.raises(InvalidSecurityResult) as error:
        finding(**overrides)
    assert field in error.value.details["fields"]


def test_findings_round_trip_and_carry_no_score() -> None:
    original = finding(
        boundary_ids=("zone",),
        connection_ids=("web-api",),
        assumptions=("The declared mechanism is implemented correctly.",),
        missing=("api.configuration.authorization",),
    )
    data = original.to_dict()
    assert SecurityFinding.from_dict(data) == original
    assert {"score", "risk", "likelihood", "exploitability"}.isdisjoint(data)
    assert (data["basis"], data["category"]) == ("control_gap", "authentication")


# --- checks --------------------------------------------------------------------------------------


def test_missing_evidence_or_an_unsupported_condition_is_never_success() -> None:
    with pytest.raises(InvalidSecurityResult):
        check(missing=("db.configuration.encryption_at_rest",))
    with pytest.raises(InvalidSecurityResult):
        check(condition=Condition.UNSUPPORTED)
    with pytest.raises(InvalidSecurityResult):  # unsupported is not a violation either
        check(condition=Condition.UNSUPPORTED, verdict=Verdict.VIOLATED)
    unsupported = check(
        key="requirement.r1",
        source=CheckSource.REQUIREMENT,
        condition=Condition.UNSUPPORTED,
        verdict=Verdict.NOT_VERIFIABLE,
        explanation="The requirement's words map to no supported condition.",
        policy_rule=None,
        requirement_id="r1",
    )
    assert CheckResult.from_dict(unsupported.to_dict()) == unsupported


def test_a_check_names_its_requirement_or_its_policy_rule() -> None:
    with pytest.raises(InvalidSecurityResult):
        check(requirement_id="r1")  # a policy check has no requirement
    with pytest.raises(InvalidSecurityResult):
        check(source=CheckSource.REQUIREMENT)  # a requirement check needs its requirement, not a rule
    mapped = check(
        key="requirement.r1",
        source=CheckSource.REQUIREMENT,
        requirement_id="r1",
        policy_rule=None,
        mapping="encryption + 'at rest'",
    )
    assert mapped.to_dict()["mapping"] == "encryption + 'at rest'"


# --- results -------------------------------------------------------------------------------------


def component(node_id: str, *, inputs: int = 1, missing: tuple[str, ...] = ()) -> ComponentResult:
    evidence = tuple(Evidence(f"configuration.p{i}", "x") for i in range(inputs))
    return ComponentResult(node_id, evidence, missing)


@pytest.mark.parametrize(
    ("components", "unsupported", "status"),
    [
        ((), (), SecurityStatus.UNSUPPORTED),
        (
            (component("a", inputs=0, missing=("configuration.exposure",)),),
            (),
            SecurityStatus.INSUFFICIENT_INPUT,
        ),
        ((component("a"), component("b", missing=("configuration.exposure",))), (), SecurityStatus.PARTIAL),
        (
            (component("a"),),
            (Unsupported("trust-boundaries", "analyzer_failed", "It failed."),),
            SecurityStatus.PARTIAL,
        ),
        ((component("a"), component("b")), (), SecurityStatus.COMPLETED),
    ],
)
def test_the_status_says_what_was_modeled(
    components: tuple[ComponentResult, ...], unsupported: tuple[Unsupported, ...], status: SecurityStatus
) -> None:
    assert SecurityResult(ANALYZERS, "f" * 64, components, unsupported=unsupported).status is status


def test_coverage_of_a_component() -> None:
    assert component("a").coverage is Coverage.MODELED
    assert component("a", missing=("configuration.exposure",)).coverage is Coverage.PARTIAL
    assert component("a", inputs=0, missing=("configuration.exposure",)).coverage is Coverage.NOT_MODELED


def test_results_are_ordered_deduplicated_fingerprinted_and_round_trip() -> None:
    low = finding(type=FindingType.EXPOSURE_NOT_MODELED, severity=Severity.LOW, node_ids=("db",))
    high = finding()
    duplicate = finding(title="Same identity, other text")
    result = SecurityResult(
        ANALYZERS,
        "f" * 64,
        components=(component("db"), component("api")),
        trust_zones=(TrustZone("zone", None, ("db", "api")),),
        findings=(low, duplicate, high),
        checks=(
            check(),
            check(
                key="policy.require_tls", condition=Condition.ENCRYPTION_IN_TRANSIT, policy_rule="require_tls"
            ),
        ),
    )
    assert [f.severity for f in result.findings] == [Severity.HIGH, Severity.LOW]
    assert [c.node_id for c in result.components] == ["api", "db"]
    assert [c.key for c in result.checks] == ["policy.require_encryption_at_rest", "policy.require_tls"]
    reordered = SecurityResult(
        ANALYZERS,
        "f" * 64,
        components=(component("api"), component("db")),
        trust_zones=(TrustZone("zone", None, ("api", "db")),),
        findings=(high, duplicate, low),
        checks=tuple(reversed(result.checks)),
    )
    assert reordered.fingerprint == result.fingerprint
    assert SecurityResult.from_dict(result.to_dict()) == result
    summary = result.summary()
    assert summary["bases"] == {"control_gap": 1, "potential_risk": 0, "violation": 0, "not_evaluable": 1}
    assert summary["checks"]["satisfied"] == 2
    assert "score" not in summary


def test_duplicate_components_checks_and_zones_are_refused() -> None:
    with pytest.raises(InvalidSecurityResult):
        SecurityResult(ANALYZERS, "f" * 64, components=(component("a"), component("a")))
    with pytest.raises(InvalidSecurityResult):
        SecurityResult(ANALYZERS, "f" * 64, checks=(check(), check()))
    with pytest.raises(InvalidSecurityResult):
        SecurityResult(ANALYZERS, "f" * 64, trust_zones=(TrustZone("z", None), TrustZone("z", "internal")))


# --- request and lifecycle -----------------------------------------------------------------------


def test_the_request_is_canonical_and_bounded() -> None:
    request = SecurityAnalysisRequest(
        uuid.UUID(int=1),
        2,
        scope=("db", "api", "db"),
        analyzers=("exposure", "authentication"),
        assumptions=(SecurityAssumption("waf", "A WAF filters public traffic."),),
    )
    assert request.scope == ("api", "db")
    assert request.analyzers == ("authentication", "exposure")
    assert request.inputs() == {
        "architecture_id": str(uuid.UUID(int=1)),
        "revision_number": 2,
        "scope": ["api", "db"],
        "analyzers": ["authentication", "exposure"],
        "assumptions": [{"key": "waf", "statement": "A WAF filters public traffic."}],
    }
    assert SecurityAnalysisRequest(uuid.UUID(int=1), 1).inputs()["analyzers"] is None


@pytest.mark.parametrize(
    ("overrides", "field", "reason"),
    [
        ({"revision_number": 0}, "revision_number", "not_a_positive_count"),
        ({"scope": tuple(f"n{i}" for i in range(201))}, "scope", "too_many"),
        ({"scope": ("",)}, "scope", "invalid_reference"),
        ({"analyzers": ()}, "analyzers", "empty"),
        ({"analyzers": ("Bad Id",)}, "analyzers", "invalid_reference"),
        (
            {"assumptions": (SecurityAssumption("a", "x"), SecurityAssumption("a", "y"))},
            "assumptions",
            "duplicate_key",
        ),
        ({"label": " "}, "label", "invalid_text"),
    ],
)
def test_invalid_requests_are_refused(overrides: dict[str, Any], field: str, reason: str) -> None:
    fields: dict[str, Any] = {"architecture_id": uuid.UUID(int=1), "revision_number": 1}
    with pytest.raises(InvalidSecurityRequest) as error:
        SecurityAnalysisRequest(**(fields | overrides))
    assert error.value.details == {"field": field, "reason": reason}


def test_the_lifecycle_ends_in_what_the_result_established() -> None:
    analysis = SecurityAnalysis(
        uuid.UUID(int=9), uuid.UUID(int=8), uuid.UUID(int=1), 1, "c" * 64, "pending", None, AT
    )
    running = analysis.start(AT)
    done = running.finish(SecurityResult(ANALYZERS, "f" * 64, (component("a"),)), AT)
    assert (done.status, done.finished) == ("completed", True)
    with pytest.raises(InvalidSecurityAnalysisTransition):
        done.start(AT)
    failed = running.fail(SecurityAnalysisError("engine_error", "The analysis could not be completed."), AT)
    assert failed.status == "failed"
    assert dataclasses.replace(analysis, status="failed").finished
