"""Hardening (Milestone 6, phase 7): the largest architecture the IR allows validates completely
and deterministically, with every finding within the result contract's limits."""

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import MAX_CONNECTIONS, MAX_NODES, ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.validation.options import RevisionInfo, ValidationConfig
from core.domain.validation.results import MAX_REFERENCES
from engines.validation.service import DeterministicValidationEngine


def largest() -> ArchitectureIR:
    """Every node breaks policy and configuration rules; every connection is a synchronous call
    with tls off and retries without a timeout, forming large request cycles."""
    nodes = tuple(
        Node(
            f"n{i:04d}",
            NodeKind.SERVICE,
            f"Service {i}",
            technology=Technology("mongodb"),
            configuration=Configuration(
                {"region": "us-east-1", "replicas": 1, "autoscaling_min_replicas": 2},
                unknown={"cpu_limit_cores"},
            ),
        )
        for i in range(MAX_NODES)
    )

    def target(j: int) -> str:
        candidate = (j * 7 + 1) % MAX_NODES
        return f"n{candidate if candidate != j % MAX_NODES else (j + 1) % MAX_NODES:04d}"

    connections = tuple(
        Connection(
            f"c{j:05d}",
            f"n{j % MAX_NODES:04d}",
            target(j),
            ConnectionKind.REQUEST,
            protocol=f"p{j}",
            interaction=Interaction.SYNCHRONOUS,
            configuration=Configuration({"tls": False, "retries": 3}),
        )
        for j in range(MAX_CONNECTIONS)
    )
    return ArchitectureIR("Largest", nodes=nodes, connections=connections)


def test_the_largest_architecture_validates_completely_and_deterministically() -> None:
    ir = largest()
    policy = ArchitecturePolicy(
        prohibited_technologies=frozenset({"mongodb"}),
        allowed_regions=frozenset({"eu-west-1"}),
        require_tls=True,
        max_components=10,
    )
    engine = DeterministicValidationEngine()
    revision = RevisionInfo("largest", 1, "0" * 64)

    first = engine.validate(
        ir, revision, requirements=(), policy=policy, config=ValidationConfig(profile="strict")
    )
    again = engine.validate(
        ir, revision, requirements=(), policy=policy, config=ValidationConfig(profile="strict")
    )

    assert first.failures == ()
    assert first.fingerprint == again.fingerprint
    assert first.summary.total > 10_000
    assert all(len(f.entity_ids) <= MAX_REFERENCES for f in first.findings)
    assert {f.rule_id for f in first.findings} >= {
        "structure.synchronous-cycle",
        "policy.technology",
        "policy.region",
        "policy.tls",
        "policy.component-count",
        "configuration.replicas-autoscaling",
        "configuration.retries-without-timeout",
        "completeness.unknown-values",
    }


SECRET = "hunter2-do-not-leak"


def test_secret_values_never_reach_findings_or_verdicts() -> None:
    """Rules report named IR fields only: settings kept in ``extra``, labels in ``metadata`` and
    descriptions never appear in a finding, whatever their names."""
    base = largest()
    leaky = tuple(
        Node(
            n.id,
            n.kind,
            n.name,
            description=f"uses {SECRET}",
            technology=n.technology,
            configuration=Configuration(
                dict(n.configuration.values),
                unknown=n.configuration.unknown,
                extra={"db_password": SECRET, "connection_string": f"postgres://u:{SECRET}@h/db"},
            ),
            metadata={"api_key": SECRET},
        )
        for n in base.nodes[:50]
    )
    ir = ArchitectureIR(
        "Leaky",
        nodes=leaky,
        connections=tuple(c for c in base.connections if c.source_id < "n0050" and c.target_id < "n0050"),
    )
    policy = ArchitecturePolicy(prohibited_technologies=frozenset({"mongodb"}), require_tls=True)
    result = DeterministicValidationEngine().validate(
        ir, RevisionInfo("leaky", 1, "0" * 64), requirements=(), policy=policy, config=ValidationConfig()
    )
    assert result.findings
    assert SECRET not in repr([f.to_dict() for f in result.findings])
    assert SECRET not in repr([r.to_dict() for r in result.requirement_results])
