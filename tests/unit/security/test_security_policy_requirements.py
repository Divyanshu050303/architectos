"""Security policy and requirement evaluation (Milestone 10, phase 8): fixed conditions judged from
declared facts, missing evidence never success, unsupported requirements identified, stable
requirement links and the mapping used recorded."""

import dataclasses
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.traceability import RequirementRef
from core.domain.projects.errors import InvalidArchitecturePolicy
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import NewRequirement, Requirement
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementScope,
    RequirementStatus,
    RequirementType,
)
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.results import (
    CheckResult,
    CheckSource,
    Condition,
    FindingType,
    SecurityFinding,
    SecurityResult,
)
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity, Verdict
from engines.security.context import SecurityContext
from engines.security.engine import Registry, analyze
from engines.security.policy import Policy
from engines.security.requirements import Requirements
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
REVISION = RevisionInfo("arch-1", 1, "c" * 64)
PROJECT = uuid.UUID(int=77)
NOW = datetime(2026, 9, 27, tzinfo=UTC)
CHECKS = Registry([Policy(), Requirements()])


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def data_access(source: str, target: str, **values: Any) -> Connection:
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=ConnectionKind.DATA_ACCESS,
        protocol="postgresql",
        configuration=Configuration(values),
    )


def requirement(
    number: int,
    category: str,
    statement: str,
    *,
    priority: RequirementPriority = RequirementPriority.HIGH,
    scope: RequirementScope = RequirementScope.SYSTEM,
    status: RequirementStatus = RequirementStatus.ACTIVE,
) -> Requirement:
    created = NewRequirement.create(
        project_id=PROJECT,
        created_by_user_id=uuid.uuid7(),
        type=RequirementType.SECURITY,
        category=category,
        title=statement[:40],
        statement=statement,
        priority=priority,
        status=RequirementStatus.ACTIVE,
    )
    content = dataclasses.replace(created.content, scope=scope, status=status)
    return Requirement(
        id=uuid.UUID(int=number),
        project_id=PROJECT,
        number=number,
        version=1,
        content=content,
        source=created.source,
        confidence=created.confidence,
        created_by_user_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def run(
    nodes: Sequence[Node],
    links: Sequence[Connection] = (),
    *,
    policy: ArchitecturePolicy | None = None,
    requirements: Sequence[Requirement] = (),
) -> SecurityResult:
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    context = SecurityContext(
        ir,
        REVISION,
        SecurityAnalysisRequest(uuid.UUID(int=1), 1),
        policy=policy or ArchitecturePolicy(),
        requirements=tuple(requirements),
    )
    return analyze(context, CHECKS)


def check(result: SecurityResult, key: str) -> CheckResult:
    [found] = [c for c in result.checks if c.key == key]
    return found


def findings(result: SecurityResult, type_: FindingType) -> list[SecurityFinding]:
    return [f for f in result.findings if f.type is type_]


# --- the policy ----------------------------------------------------------------------------------


def test_an_empty_policy_checks_nothing() -> None:
    assert run([component("db", NodeKind.DATABASE)]).checks == ()


@pytest.mark.parametrize(
    ("values", "verdict"),
    [
        ({"encryption_at_rest": True}, Verdict.SATISFIED),
        ({"encryption_at_rest": False}, Verdict.VIOLATED),
        ({}, Verdict.NOT_VERIFIABLE),  # missing evidence is never success
    ],
)
def test_encryption_at_rest_by_policy(values: dict[str, Any], verdict: Verdict) -> None:
    policy = ArchitecturePolicy(require_encryption_at_rest=True)
    result = run([component("db", NodeKind.DATABASE, **values), component("api")], policy=policy)
    found = check(result, "policy.require_encryption_at_rest")
    assert (found.source, found.condition, found.verdict, found.policy_rule) == (
        CheckSource.POLICY,
        Condition.ENCRYPTION_AT_REST,
        verdict,
        "require_encryption_at_rest",
    )
    assert found.node_ids == ("db",)  # only data stores are concerned
    if verdict is Verdict.VIOLATED:
        [violation] = findings(result, T.POLICY_VIOLATED)
        assert (violation.severity, violation.policy_rule, violation.node_ids) == (
            Severity.HIGH,
            "require_encryption_at_rest",
            ("db",),
        )
    if verdict is Verdict.NOT_VERIFIABLE:
        [unknown] = findings(result, T.POLICY_NOT_EVALUABLE)
        assert (unknown.severity, unknown.missing) == (
            Severity.MEDIUM,
            ("db.configuration.encryption_at_rest",),
        )
        assert found.missing == ("db.configuration.encryption_at_rest",)


def test_a_rule_with_nothing_to_check_is_not_applicable() -> None:
    result = run([component("api")], policy=ArchitecturePolicy(require_encryption_at_rest=True))
    assert check(result, "policy.require_encryption_at_rest").verdict is Verdict.NOT_APPLICABLE
    assert result.findings == ()


def test_what_decides_whether_a_rule_applies_must_be_declared_too() -> None:
    policy = ArchitecturePolicy(require_authentication_on_public=True)
    key = "policy.require_authentication_on_public"
    unknown = run([component("api")], policy=policy)  # is it public? not declared
    assert check(unknown, key).verdict is Verdict.NOT_VERIFIABLE
    internal = run([component("api", exposure="internal")], policy=policy)
    assert check(internal, key).verdict is Verdict.NOT_APPLICABLE
    open_api = run([component("api", exposure="public", authentication="none")], policy=policy)
    assert check(open_api, key).verdict is Verdict.VIOLATED


def test_a_known_violation_stands_when_other_elements_are_unknown() -> None:
    policy = ArchitecturePolicy(prohibit_public_management_interfaces=True)
    nodes = [component("admin", exposure="public", management_interface=True), component("api")]
    result = run(nodes, policy=policy)
    assert check(result, "policy.prohibit_public_management_interfaces").verdict is Verdict.VIOLATED
    [violation] = findings(result, T.POLICY_VIOLATED)
    assert violation.node_ids == ("admin",)


def test_approved_secret_sources_and_rotation() -> None:
    policy = ArchitecturePolicy(
        approved_secret_sources=frozenset({"secret_manager"}), require_secret_rotation=True
    )
    nodes = [
        component("api", secrets_required=True, secret_source="environment", secret_rotation=True),
        component("jobs", NodeKind.WORKER, secrets_required=False),
    ]
    result = run(nodes, policy=policy)
    assert check(result, "policy.approved_secret_sources").verdict is Verdict.VIOLATED
    assert check(result, "policy.require_secret_rotation").verdict is Verdict.SATISFIED


def test_classification_and_audit_logging_must_be_modeled() -> None:
    policy = ArchitecturePolicy(require_data_classification=True, require_audit_logging=True)
    nodes = [component("api", audit_logging=True), component("psp", NodeKind.EXTERNAL)]
    result = run(nodes, policy=policy)
    classification = check(result, "policy.require_data_classification")
    assert classification.verdict is Verdict.VIOLATED  # the rule is that it is modeled
    assert classification.node_ids == ("api", "psp")
    assert check(result, "policy.require_audit_logging").node_ids == ("api",)  # third parties aside


def test_require_tls_is_reported_with_validations_meaning() -> None:
    policy = ArchitecturePolicy(require_tls=True)
    nodes = [component("api"), component("db", NodeKind.DATABASE)]
    encrypted = run(nodes, [data_access("api", "db", tls=True)], policy=policy)
    assert check(encrypted, "policy.require_tls").verdict is Verdict.SATISFIED
    unstated = run(nodes, [data_access("api", "db")], policy=policy)
    assert check(unstated, "policy.require_tls").missing == ("api-db.configuration.tls",)


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ({"approved_secret_sources": ["vault"]}, "approved_secret_sources"),
        ({"require_audit_logging": "yes"}, "require_audit_logging"),
    ],
)
def test_invalid_security_policy_fields_are_refused(raw: dict[str, Any], field: str) -> None:
    with pytest.raises(InvalidArchitecturePolicy) as error:
        ArchitecturePolicy.from_dict(raw)
    assert error.value.details["field"] == field


# --- requirements --------------------------------------------------------------------------------


def test_a_requirement_is_checked_through_the_documented_mapping() -> None:
    req = requirement(
        12, "encryption", "Customer data must be encrypted at rest.", priority=RequirementPriority.CRITICAL
    )
    db = component("db", NodeKind.DATABASE, personal_data=True, encryption_at_rest=False)
    cache = component("cache", NodeKind.CACHE, data_classification="public")
    result = run([db, cache], requirements=[req])
    found = check(result, "requirement.req-12")
    assert (found.condition, found.verdict, found.requirement_id) == (
        Condition.ENCRYPTION_AT_REST,
        Verdict.VIOLATED,
        str(req.id),
    )
    assert found.mapping == "encryption + 'at rest' (sensitive only)"
    assert found.node_ids == ("db",)  # "customer data": only what is declared sensitive
    [violation] = findings(result, T.REQUIREMENT_VIOLATED)
    assert (violation.severity, violation.requirement_id) == (Severity.CRITICAL, str(req.id))


def test_one_requirement_can_ask_two_things() -> None:
    req = requirement(3, "encryption", "All data is encrypted at rest and in transit (TLS).")
    nodes = [component("api"), component("db", NodeKind.DATABASE, encryption_at_rest=True)]
    result = run(nodes, [data_access("api", "db", tls=True)], requirements=[req])
    assert {c.key: c.verdict for c in result.checks} == {
        "requirement.req-3.encryption_at_rest": Verdict.SATISFIED,
        "requirement.req-3.encryption_in_transit": Verdict.SATISFIED,
    }


@pytest.mark.parametrize(
    ("category", "statement"),
    [
        ("authentication", "Users sign in with SSO and MFA."),
        ("secrets", "API keys are handled carefully."),
        ("pii", "Personal data is minimized."),  # pii maps to encryption only when it says so
    ],
)
def test_words_that_match_no_condition_are_unsupported_never_satisfied(category: str, statement: str) -> None:
    api = component("api", exposure="public", authentication="oauth2")
    result = run([api], requirements=[requirement(5, category, statement)])
    [found] = result.checks
    assert (found.condition, found.verdict, found.mapping) == (
        Condition.UNSUPPORTED,
        Verdict.NOT_VERIFIABLE,
        None,
    )
    assert result.findings == ()  # listed as a check, nothing claimed about the architecture


def test_requirements_follow_their_scope_and_references() -> None:
    by_scope = requirement(
        7, "encryption", "Databases are encrypted at rest.", scope=RequirementScope.DATABASE
    )
    db = component("db", NodeKind.DATABASE, encryption_at_rest=True)
    queue = component("q", NodeKind.QUEUE, encryption_at_rest=False)
    assert check(run([db, queue], requirements=[by_scope]), "requirement.req-7").node_ids == ("db",)
    referenced = requirement(8, "encryption", "This store is encrypted at rest.")
    tagged = dataclasses.replace(queue, requirement_refs=(RequirementRef(referenced.id, 1),))
    found = check(run([db, tagged], requirements=[referenced]), "requirement.req-8")
    assert (found.node_ids, found.verdict) == (("q",), Verdict.VIOLATED)
    by_user = requirement(
        9, "authentication", "Public endpoints require authentication.", scope=RequirementScope.USER
    )
    assert (
        check(run([db, queue], requirements=[by_user]), "requirement.req-9").condition
        is Condition.UNSUPPORTED
    )


def test_each_condition_of_a_requirement_is_its_own_finding() -> None:
    """Review finding: two conditions violated on the same element shared one finding id, and one
    violation was dropped."""
    req = requirement(
        1, "authorization", "All admin operations on sensitive resources must be authorized and audited"
    )
    nodes = [component("admin-api", sensitive_operations=True, authorization="none", audit_logging=False)]
    result = run(nodes, requirements=[req])
    violated = findings(result, T.REQUIREMENT_VIOLATED)
    assert sorted(f.check_key or "" for f in violated) == [
        "requirement.req-1.audit_logging",
        "requirement.req-1.authorization_on_sensitive",
    ]
    assert {c.key for c in result.checks} == {f.check_key for f in violated}  # each finding names its check


def test_unencrypted_speaks_of_encrypting() -> None:
    """Review finding: "unencrypted" was not recognized, so the requirement went unchecked."""
    req = requirement(2, "pii", "Personal data must never be stored unencrypted in the database.")
    db = component("db", NodeKind.DATABASE, personal_data=True, encryption_at_rest=False)
    found = check(run([db], requirements=[req]), "requirement.req-2")
    assert (found.condition, found.verdict) == (Condition.ENCRYPTION_AT_REST, Verdict.VIOLATED)


def test_only_security_requirements_in_force_are_evaluated() -> None:
    draft = requirement(4, "encryption", "Data is encrypted at rest.", status=RequirementStatus.DRAFT)
    assert run([component("db", NodeKind.DATABASE)], requirements=[draft]).checks == ()


def test_requirement_links_are_stable() -> None:
    req = requirement(12, "authorization", "Sensitive operations require authorization.")
    nodes = [component("pay", sensitive_operations=True, authorization="none")]
    first, again = run(nodes, requirements=[req]), run(nodes, requirements=[req])
    assert first.to_dict() == again.to_dict()
    [violation] = findings(first, T.REQUIREMENT_VIOLATED)
    assert violation.id == findings(again, T.REQUIREMENT_VIOLATED)[0].id
