"""Constraint evaluation (ARCH-COMP-001, phase 4): each node that refers to a catalog component is
evaluated against its specification — pass, warning, violation, cannot_evaluate, not_applicable —
with the specification version, the constraint's evidence and a stable finding id; unknown data is
never a pass; no throughput is concluded from a configuration; identical inputs give identical
findings, and an earlier specification version can be re-used exactly."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.serialization import content_hash
from core.domain.components.evaluation import ConstraintEvaluation, Outcome
from core.domain.components.repository import ComponentCatalog
from core.domain.components.specifications import ComponentSpecification
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.constraints.service import DeterministicConstraintEngine
from persistence.component_catalog import default_catalog
from tests.unit.architecture_ir.builders import node
from tests.unit.components.test_component_specifications import DOCUMENTED, spec

ENGINE = DeterministicConstraintEngine()
GIB = 1024**3
IDENTITY = {"id", "version", "name", "category", "technology", "node_kinds", "description", "provider"}


def component(
    node_id: str, kind: NodeKind, ref: str | None, unknown: tuple[str, ...] = (), **values: Any
) -> Node:
    return node(node_id, kind, component=ref, configuration=Configuration(values, unknown=frozenset(unknown)))


def evaluate(*nodes: Node, catalog: ComponentCatalog | None = None, **kwargs: Any) -> ConstraintEvaluation:
    ir = ArchitectureIR("Components", nodes=nodes)
    revision = RevisionInfo(str(uuid.UUID(int=5)), 1, content_hash(ir))
    return ENGINE.evaluate_architecture(ir, revision, catalog or default_catalog(), **kwargs)


def outcomes(evaluation: ConstraintEvaluation) -> dict[tuple[str, str], Outcome]:
    return {(f.node_id, f.check): f.outcome for f in evaluation.findings}


def test_a_documented_limit_passes_or_is_violated_with_its_evidence() -> None:
    result = evaluate(
        component("orders", NodeKind.QUEUE, "messaging/aws-sqs", retention_seconds=345_600),
        component("audit", NodeKind.QUEUE, "messaging/aws-sqs", retention_seconds=30),
    )
    passed = next(f for f in result.findings if f.node_id == "orders")
    assert (passed.outcome, passed.severity, passed.actual) == (Outcome.PASS, None, 345_600)
    assert passed.expected == "between 60 and 1209600"
    assert (passed.unit, passed.specification, passed.constraint_type) == (
        "s",
        "messaging/aws-sqs@2",
        "hard_limit",
    )
    assert passed.provenance is not None
    assert passed.provenance.kind.value == "documented"
    violated = next(f for f in result.findings if f.node_id == "audit")
    assert (violated.outcome, violated.severity, violated.actual) == (Outcome.VIOLATION, Severity.HIGH, 30)
    assert violated.remediation
    assert result.specifications == {
        "messaging/aws-sqs@2": default_catalog().get("messaging/aws-sqs").content_hash
    }


def test_lambda_memory_is_checked_against_the_documented_range() -> None:
    result = evaluate(
        component("small", NodeKind.SERVICE, "compute/aws-lambda", memory_limit_bytes=512 * 1024**2),
        component("huge", NodeKind.SERVICE, "compute/aws-lambda", memory_limit_bytes=20 * GIB),
    )
    assert outcomes(result)[("small", "memory_allocation")] is Outcome.PASS
    assert outcomes(result)[("huge", "memory_allocation")] is Outcome.VIOLATION


def test_unknown_data_is_never_a_pass() -> None:
    result = evaluate(
        component("silent", NodeKind.QUEUE, "messaging/aws-sqs"),
        component("unread", NodeKind.QUEUE, "messaging/aws-sqs", unknown=("retention_seconds",)),
        component("mystery", NodeKind.DATABASE, "databases/quantum-db"),
        component("planned", NodeKind.DATABASE, "databases/mysql"),
    )
    found = {f.node_id: f for f in result.findings}
    assert found["silent"].outcome is Outcome.CANNOT_EVALUATE
    assert "not stated" in found["silent"].explanation
    assert "marked unknown" in found["unread"].explanation
    assert (found["mystery"].check, found["mystery"].specification) == ("component_not_in_catalog", None)
    assert (found["planned"].check, found["planned"].outcome) == (
        "specification_planned",
        Outcome.CANNOT_EVALUATE,
    )
    assert result.summary()["pass"] == 0


def test_the_spec_example_concludes_nothing_the_catalog_does_not_state() -> None:
    """ARCH-COMP-001 section 8: 8 CPUs, 32 GB, 1 TB and 2,000 connections on PostgreSQL — the
    specification documents no limit for these, so nothing passes, fails or is predicted."""
    db = component(
        "db",
        NodeKind.DATABASE,
        "databases/postgresql",
        cpu_limit_cores=Decimal(8),
        memory_limit_bytes=32 * GIB,
        storage_bytes=1000 * GIB,
        max_connections=2000,
    )
    assert evaluate(db).findings == ()
    [needed] = evaluate(component("db", NodeKind.DATABASE, "databases/postgresql")).findings
    assert (needed.check, needed.outcome) == (
        "required_value_missing.max_connections",
        Outcome.CANNOT_EVALUATE,
    )


def test_the_reference_itself_is_checked() -> None:
    result = evaluate(
        component("api", NodeKind.SERVICE, "databases/postgresql"),  # a service is not a database
        node("plain", NodeKind.SERVICE),  # no reference: not evaluated
        node("postgres", NodeKind.DATABASE, technology=Technology("postgresql")),  # never matched by name
    )
    [mismatch] = result.findings
    assert (mismatch.node_id, mismatch.check, mismatch.outcome) == (
        "api",
        "node_kind_not_modeled",
        Outcome.VIOLATION,
    )


def catalog_of(*specs: ComponentSpecification) -> ComponentCatalog:
    return ComponentCatalog.build(specs, {s.ref: s.content_hash for s in specs})


def constraint(identifier: str, **fields: Any) -> dict[str, Any]:
    base = {
        "id": identifier,
        "type": "hard_limit",
        "description": "A documented limit.",
        "property": "max_connections",
        "comparison": "at_most",
        "limit": 500,
        "severity": "high",
        "provenance": DOCUMENTED,
    }
    return base | fields


SYNCHRONOUS = [{"property": "replication_mode", "values": ["synchronous"]}]
SYNTHETIC = ComponentSpecification.from_dict(
    spec(
        technology_versions=["16", "17"],
        constraints=[
            constraint("recommended", type="recommended_range", comparison="between", limit=None, minimum=10,
                       maximum=200, severity="medium"),
            constraint("conditional", type="conditional_limit", conditions=SYNCHRONOUS, limit=100),
            constraint("undocumented", type="unknown", comparison=None, limit=None,
                       provenance={"kind": "unknown"}),
            constraint("versioned", technology_versions=["17"]),
            constraint("unsupported", type="unsupported_configuration", property="replication_mode",
                       comparison="not_one_of", limit=None, values=["asynchronous"]),
        ],
    )
)  # fmt: skip
W, V, P, C, N = (
    Outcome.WARNING,
    Outcome.VIOLATION,
    Outcome.PASS,
    Outcome.CANNOT_EVALUATE,
    Outcome.NOT_APPLICABLE,
)
CASES = [
    # a recommendation is never a violation; a condition that does not hold is not applicable;
    # an undocumented limit cannot be evaluated
    ({"max_connections": 300, "replication_mode": "none"}, "17",
     {"recommended": W, "conditional": N, "undocumented": C, "versioned": P, "unsupported": P}),
    # a limit checked for another version does not apply
    ({"max_connections": 300, "replication_mode": "synchronous"}, "16",
     {"recommended": W, "conditional": V, "undocumented": C, "versioned": N, "unsupported": P}),
    # the node states no version
    ({"max_connections": 50, "replication_mode": "asynchronous"}, None,
     {"recommended": P, "conditional": N, "undocumented": C, "versioned": C, "unsupported": V}),
    # nothing stated: nothing can be decided, and the required value is requested
    ({}, "17", {"recommended": C, "conditional": C, "undocumented": C, "versioned": C, "unsupported": C,
                "required_value_missing.max_connections": C}),
]  # fmt: skip


@pytest.mark.parametrize(("values", "version", "expected"), CASES)
def test_every_constraint_type_has_its_outcome(
    values: dict[str, Any], version: str | None, expected: dict[str, Outcome]
) -> None:
    technology = Technology("example-sql", version) if version else None
    configuration = Configuration(values)
    db = node("db", NodeKind.DATABASE, component="databases/example-sql", technology=technology,
              configuration=configuration)  # fmt: skip
    result = ENGINE.evaluate_node(db, SYNTHETIC, catalog_of(SYNTHETIC))
    assert {f.check: f.outcome for f in result.findings} == expected
    for finding in result.findings:
        if finding.outcome is Outcome.WARNING:
            assert finding.severity is Severity.MEDIUM


def test_a_deprecated_specification_is_a_warning_and_still_evaluated() -> None:
    identity = {k: v for k, v in spec(id="databases/next-sql").items() if k in IDENTITY}
    successor = ComponentSpecification.from_dict(identity | {"support_status": "planned"})
    old = ComponentSpecification.from_dict(
        spec(support_status="deprecated", replaced_by="databases/next-sql", constraints=[constraint("limit")])
    )
    db = component("db", NodeKind.DATABASE, "databases/example-sql", max_connections=900)
    assert outcomes(evaluate(db, catalog=catalog_of(old, successor))) == {
        ("db", "specification_deprecated"): Outcome.WARNING,
        ("db", "limit"): Outcome.VIOLATION,
    }


def test_identical_inputs_give_identical_findings_and_ids() -> None:
    nodes = (
        component("audit", NodeKind.QUEUE, "messaging/aws-sqs", retention_seconds=30),
        component("fn", NodeKind.SERVICE, "compute/aws-lambda", memory_limit_bytes=GIB),
        component("db", NodeKind.DATABASE, "databases/postgresql"),
    )
    first, again = evaluate(*nodes), evaluate(*reversed(nodes))
    assert first.to_dict() == again.to_dict()
    assert first.fingerprint == again.fingerprint
    ids = [f.id for f in first.findings]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("cst_") and len(i) == 24 for i in ids)


def test_an_earlier_specification_version_is_reused_exactly() -> None:
    queue = component("orders", NodeKind.QUEUE, "messaging/aws-sqs", retention_seconds=30)
    historical = evaluate(queue, versions={"messaging/aws-sqs": 1})
    assert list(historical.specifications) == ["messaging/aws-sqs@1"]
    [planned] = historical.findings  # version 1 specified nothing yet
    assert planned.check == "specification_planned"
    assert evaluate(queue).specifications.keys() == {"messaging/aws-sqs@2"}
