"""Configuration and policy rules, and the limitations every result states (Milestone 6, phase 4)."""

from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.validation.errors import InvalidValidationConfig
from core.domain.validation.results import Category, Finding, Severity, ValidationResult
from engines.validation.context import RevisionInfo, ValidationConfig, ValidationContext
from engines.validation.engine import CATALOG_UNAVAILABLE, NO_POLICY, NO_REQUIREMENTS, validate
from engines.validation.registry import default_registry
from tests.unit.architecture_ir.builders import api_and_postgres, connection, node, service_cache_queue

REVISION = RevisionInfo("arch-1", 1, "d" * 64)


def result(ir: ArchitectureIR, policy: ArchitecturePolicy | None = None, **config: Any) -> ValidationResult:
    context = ValidationContext(ir, REVISION, config=ValidationConfig(**config), policy=policy)
    outcome = validate(context, default_registry())
    assert outcome.failures == ()
    return outcome


def findings(ir: ArchitectureIR, policy: ArchitecturePolicy | None = None, prefix: str = "") -> list[Finding]:
    return [f for f in result(ir, policy).findings if f.rule_id.startswith(prefix)]


def one_node(kind: NodeKind = NodeKind.SERVICE, **config: Any) -> ArchitectureIR:
    return ArchitectureIR(name="One", nodes=(node("api", kind, configuration=Configuration(config)),))


# --- configuration -------------------------------------------------------------------------------


def test_the_examples_have_consistent_configuration() -> None:
    for ir in (api_and_postgres(), service_cache_queue()):
        assert findings(ir, prefix="configuration.") == []


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            {"replicas": 1, "autoscaling_min_replicas": 2, "autoscaling_max_replicas": 5},
            ["autoscaling_min_replicas"],
        ),
        (
            {"replicas": 9, "autoscaling_min_replicas": 2, "autoscaling_max_replicas": 5},
            ["autoscaling_max_replicas"],
        ),
        ({"replicas": 3, "autoscaling_min_replicas": 2, "autoscaling_max_replicas": 5}, []),
        ({"replicas": 3}, []),
    ],
)
def test_replicas_outside_the_autoscaling_range(config: dict[str, Any], expected: list[str]) -> None:
    found = findings(one_node(**config), prefix="configuration.replicas")
    assert [f.field_paths[0] for f in found] == [f"configuration.{b}" for b in expected]


def test_multi_az_and_zones_must_agree() -> None:
    [single] = findings(one_node(multi_az=True, availability_zones=("eu-west-1a",)), prefix="configuration.")
    assert (single.code, single.severity) == ("multi_az_single_zone", Severity.MEDIUM)
    [several] = findings(
        one_node(multi_az=False, availability_zones=("eu-west-1a", "eu-west-1b")), prefix="configuration."
    )
    assert (several.code, several.severity) == ("zones_without_multi_az", Severity.LOW)
    assert findings(one_node(multi_az=True, availability_zones=("a1", "b1")), prefix="configuration.") == []
    assert findings(one_node(multi_az=True), prefix="configuration.") == []  # nothing to compare


def test_backups_and_retention_must_agree() -> None:
    db = NodeKind.DATABASE
    [kept_none] = findings(
        one_node(db, backup_enabled=True, backup_retention_seconds=0), prefix="configuration."
    )
    assert kept_none.code == "backups_not_retained"
    [no_backups] = findings(
        one_node(db, backup_enabled=False, backup_retention_seconds=86400), prefix="configuration."
    )
    assert (no_backups.code, no_backups.severity) == ("retention_without_backups", Severity.LOW)
    assert (
        findings(one_node(db, backup_enabled=True, backup_retention_seconds=86400), prefix="configuration.")
        == []
    )


def _two(*connections: Any) -> ArchitectureIR:
    return ArchitectureIR(
        name="Two", nodes=(node("api"), node("db", NodeKind.DATABASE)), connections=connections
    )


def test_retries_need_a_timeout_on_connections_the_caller_waits_on() -> None:
    [found] = findings(_two(connection(configuration=Configuration({"retries": 3}))), prefix="configuration.")
    assert (found.code, found.entity_ids) == ("retries_without_timeout", ("api-db",))
    timed = Configuration({"retries": 3, "timeout_seconds": Decimal("0.5")})
    assert findings(_two(connection(configuration=timed)), prefix="configuration.") == []
    unknown = Configuration({"retries": 3}, unknown={"timeout_seconds"})
    assert (
        findings(_two(connection(configuration=unknown)), prefix="configuration.") == []
    )  # completeness reports it
    no_retries = Configuration({"retries": 0})
    assert findings(_two(connection(configuration=no_retries)), prefix="configuration.") == []
    asynchronous = Interaction.ASYNCHRONOUS
    retried = Configuration({"retries": 3})
    async_call = connection(
        kind=ConnectionKind.REQUEST, protocol="https", interaction=asynchronous, configuration=retried
    )
    assert findings(_two(async_call), prefix="configuration.") == []


def test_dead_letter_belongs_to_consumers() -> None:
    [found] = findings(
        _two(connection(configuration=Configuration({"dead_letter": True}))), prefix="configuration."
    )
    assert (found.code, found.severity) == ("dead_letter_not_consumer", Severity.LOW)
    assert findings(service_cache_queue(), prefix="configuration.") == []  # the consumer has it


# --- limitations ---------------------------------------------------------------------------------


def test_every_result_states_its_limitations() -> None:
    everything = (CATALOG_UNAVAILABLE, NO_POLICY, NO_REQUIREMENTS)
    assert result(api_and_postgres()).limitations == everything
    assert result(api_and_postgres(), ArchitecturePolicy()).limitations == everything
    strict = ArchitecturePolicy(require_tls=True)
    assert result(api_and_postgres(), strict).limitations == (CATALOG_UNAVAILABLE, NO_REQUIREMENTS)


def test_the_policy_is_part_of_the_context_fingerprint() -> None:
    plain = ValidationContext(api_and_postgres(), REVISION)
    empty = ValidationContext(api_and_postgres(), REVISION, policy=ArchitecturePolicy())
    strict = ValidationContext(api_and_postgres(), REVISION, policy=ArchitecturePolicy(require_tls=True))
    assert plain.fingerprint == empty.fingerprint  # the empty policy constrains nothing
    assert strict.fingerprint != plain.fingerprint


# --- policy --------------------------------------------------------------------------------------


def test_without_a_policy_no_policy_rule_reports_anything() -> None:
    ir = api_and_postgres()
    assert findings(ir, None, "policy.") == []
    assert findings(ir, ArchitecturePolicy(), "policy.") == []


def test_prohibited_and_unlisted_technologies_are_blocking_violations() -> None:
    [prohibited] = findings(
        api_and_postgres(), ArchitecturePolicy(prohibited_technologies=frozenset({"postgresql"})), "policy."
    )
    assert (prohibited.code, prohibited.entity_ids, prohibited.blocking) == (
        "prohibited_technology",
        ("db",),
        True,
    )
    assert (prohibited.policy_rule, prohibited.category) == ("prohibited_technologies", Category.POLICY)

    [unlisted] = findings(
        api_and_postgres(), ArchitecturePolicy(allowed_technologies=frozenset({"postgresql"})), "policy."
    )
    assert (unlisted.code, unlisted.entity_ids, unlisted.actual) == (
        "technology_not_allowed",
        ("api",),
        "fastapi",
    )
    # The web client states no technology, and need not: clients are not ours to choose.

    everything = ArchitecturePolicy(allowed_technologies=frozenset({"postgresql", "fastapi"}))
    assert findings(api_and_postgres(), everything, "policy.") == []


def test_an_unstated_technology_cannot_be_shown_to_comply() -> None:
    ir = ArchitectureIR(name="One", nodes=(node("worker", NodeKind.WORKER),))
    [found] = findings(ir, ArchitecturePolicy(allowed_technologies=frozenset({"python"})), "policy.")
    assert (found.code, found.blocking, found.severity) == ("technology_unstated", False, Severity.LOW)


def test_regions_outside_the_policy_are_blocking() -> None:
    ir = ArchitectureIR(
        name="Placed",
        nodes=(
            node("vpc", NodeKind.BOUNDARY, configuration=Configuration({"region": "us-east-1"})),
            node("api", parent_id="vpc", configuration=Configuration({"region": "eu-west-1"})),
            node("db", NodeKind.DATABASE, configuration=Configuration(unknown={"region"})),
        ),
        connections=(connection(),),
    )
    found = findings(ir, ArchitecturePolicy(allowed_regions=frozenset({"eu-west-1"})), "policy.")
    assert [(f.code, f.entity_ids, f.blocking) for f in found] == [
        ("region_not_allowed", ("vpc",), True),
        ("region_unknown", ("db",), False),
    ]


def test_tls_is_required_on_every_communicating_connection() -> None:
    tls_off = connection("api-db", configuration=Configuration({"tls": False}))
    unstated = connection("api-cache", "api", "cache", protocol="redis")
    encrypted = connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https")
    stated = connection("api-q", "api", "q", kind=ConnectionKind.PUBLISH, protocol="kafka",
                        configuration=Configuration({"tls": True}))  # fmt: skip
    dependency = connection("api-cfg", "api", "q", kind=ConnectionKind.DEPENDENCY, protocol=None)
    ir = ArchitectureIR(
        name="TLS",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("api"),
            node("db", NodeKind.DATABASE),
            node("cache", NodeKind.CACHE),
            node("q", NodeKind.QUEUE),
        ),
        connections=(tls_off, unstated, encrypted, stated, dependency),
    )
    found = findings(ir, ArchitecturePolicy(require_tls=True), "policy.")
    assert [(f.code, f.entity_ids, f.blocking, f.severity) for f in found] == [
        ("tls_disabled", ("api-db",), True, Severity.HIGH),
        ("tls_unstated", ("api-cache",), False, Severity.MEDIUM),
    ]
    assert findings(ir, ArchitecturePolicy(), "policy.") == []


def test_the_component_count_is_capped_boundaries_excluded() -> None:
    ir = api_and_postgres(nodes=(*api_and_postgres().nodes, node("vpc", NodeKind.BOUNDARY)))
    assert findings(ir, ArchitecturePolicy(max_components=3), "policy.") == []
    [found] = findings(ir, ArchitecturePolicy(max_components=2), "policy.")
    assert (found.code, found.actual, found.expected, found.blocking) == (
        "too_many_components",
        "3",
        "at most 2",
        True,
    )


def test_policy_rules_are_mandatory() -> None:
    policy = ArchitecturePolicy(prohibited_technologies=frozenset({"postgresql"}))
    chosen = result(api_and_postgres(), policy, rules=())
    assert [f.code for f in chosen.findings] == ["prohibited_technology"]  # ran although not selected
    with pytest.raises(InvalidValidationConfig) as raised:
        result(api_and_postgres(), policy, severity_overrides={"policy.technology": Severity.INFO})
    assert raised.value.details["reason"] == "mandatory_rule"


def test_blocking_findings_are_counted() -> None:
    policy = ArchitecturePolicy(prohibited_technologies=frozenset({"postgresql", "fastapi"}))
    assert result(api_and_postgres(), policy).summary.blocking == 2


def test_technology_names_compare_normalized() -> None:
    ir = ArchitectureIR(
        name="One", nodes=(node("db", NodeKind.DATABASE, technology=Technology("PostgreSQL")),)
    )
    policy = ArchitecturePolicy.from_dict({"prohibited_technologies": ["POSTGRESQL"]})
    assert [f.code for f in findings(ir, policy, "policy.")] == ["prohibited_technology"]
