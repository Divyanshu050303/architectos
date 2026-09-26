"""Reliability findings (Milestone 9, phase 6): what failures reach, what paths cannot establish,
unconfirmed data, and the quality every finding must have: evidence, why it matters, what is missing,
options for review; reproducible, without scores or claims of outages."""

import uuid
from decimal import Decimal
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.domain.capacity.results import Certainty
from core.domain.engine_results import Evidence
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.reliability.results import FindingType, ReliabilityFinding, ReliabilityResult
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.reliability.context import ReliabilityContext
from engines.reliability.engine import analyze
from engines.reliability.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
T = FindingType
SYNC: dict[str, Any] = {
    "kind": ConnectionKind.REQUEST,
    "protocol": "https",
    "interaction": Interaction.SYNCHRONOUS,
}


def comp(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Any:
    extra = {k: values.pop(k) for k in ("field_provenance",) if k in values}
    return node(node_id, kind, configuration=Configuration(values), **extra)


def call(source: str, target: str, **overrides: Any) -> Any:
    return connection(f"{source}-{target}", source, target, **(SYNC | overrides))


def run(nodes: tuple[Any, ...], links: tuple[Any, ...]) -> ReliabilityResult:
    ir = ArchitectureIR("Shop", nodes=nodes, connections=links)
    context = ReliabilityContext(ir, REVISION, ReliabilityAnalysisRequest(uuid.UUID(int=1), 1))
    return analyze(context, default_registry())


def of(result: ReliabilityResult, kind: FindingType) -> list[ReliabilityFinding]:
    return [f for f in result.findings if f.type is kind]


def data(source: str, target: str, **overrides: Any) -> Any:
    return call(source, target, kind=ConnectionKind.DATA_ACCESS, protocol="postgresql", **overrides)


def bus(source: str, kind: ConnectionKind) -> Any:
    return call(source, "bus", kind=kind, protocol="kafka", interaction=None)


MEMBER: dict[str, Any] = {
    "redundancy_group": "api",
    "redundancy_group_min_healthy": 1,
    "failure_independence": "independent",
    "failover_mode": "automatic",
    "failover_seconds": 60,
    "replicas": 2,
    "min_healthy_replicas": 1,
    "replica_availability": Decimal("0.99"),
    "availability_zones": ("a", "b"),
}
DB: dict[str, Any] = {
    "replicas": 1,
    "mtbf_seconds": 720000,
    "mttr_seconds": 3600,
    "replication_mode": "asynchronous",
}


def shop() -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """A realistic architecture: two entries, a group of regions, a single database, a queue."""
    nodes = (
        node("web", NodeKind.CLIENT),
        node("admin", NodeKind.CLIENT),
        comp(
            "lb", NodeKind.LOAD_BALANCER, availability=Decimal("0.9999"), replicas=2, min_healthy_replicas=1
        ),
        comp("eu", region="eu-west-1", **MEMBER),
        comp("us", region="us-east-1", **MEMBER),
        comp("db", NodeKind.DATABASE, **DB),
        comp("bus", NodeKind.QUEUE),
        comp("mailer", NodeKind.WORKER),
        comp("backoffice"),
        comp("payments", NodeKind.EXTERNAL),
    )
    links = (
        call("web", "lb"),
        call("lb", "eu"),
        call("lb", "us"),
        data("eu", "db"),
        data("us", "db"),
        call("eu", "payments", critical=True),
        call("us", "payments", critical=True),
        bus("eu", ConnectionKind.PUBLISH),
        bus("mailer", ConnectionKind.CONSUME),
        call("admin", "backoffice"),
        data("backoffice", "db", interaction=None),
    )
    return nodes, links


def test_a_single_point_lists_everything_its_failure_can_reach() -> None:
    [db] = [f for f in of(run(*shop()), T.SINGLE_POINT_OF_FAILURE) if f.node_ids == ("db",)]
    evidence = {e.label: e.value for e in db.evidence if e.label in {"affects", "configuration.replicas"}}
    assert evidence == {"affects": "admin, backoffice, eu, lb, us, web", "configuration.replicas": "1"}
    assert [e.value for e in db.evidence if e.label == "needed_by"] == ["admin", "web"]
    assert db.severity is Severity.HIGH  # every entry's path needs it


def test_a_critical_dependency_lists_what_it_affects() -> None:
    critical = of(run(*shop()), T.CRITICAL_DEPENDENCY_WITHOUT_ALTERNATIVE)
    assert {(f.connection_ids, f.certainty) for f in critical} == {
        (("eu-payments",), Certainty.CANDIDATE), (("us-payments",), Certainty.CANDIDATE),
    }  # fmt: skip
    assert Evidence("affects", "eu, lb, us, web") in critical[0].evidence


def test_paths_whose_availability_cannot_be_estimated_say_what_they_lack() -> None:
    result = run(*shop())
    by_entry = {
        e.value: f for f in of(result, T.AVAILABILITY_NOT_EVALUABLE) for e in f.evidence if e.label == "entry"
    }
    assert set(by_entry) == {"admin", "web"}
    assert by_entry["web"].missing == ("payments.availability",)
    assert by_entry["web"].node_ids == ("payments",)
    assert by_entry["admin"].missing == ("backoffice.availability",)
    assert by_entry["admin"].certainty is Certainty.MODELED


def test_a_known_path_has_no_such_finding() -> None:
    nodes = (node("web", NodeKind.CLIENT), comp("api", availability=Decimal("0.999")))
    assert of(run(nodes, (call("web", "api"),)), T.AVAILABILITY_NOT_EVALUABLE) == []


def test_alternatives_that_cannot_be_composed_are_named() -> None:
    nodes, links = shop()
    loose: dict[str, Any] = {
        "redundancy_group": "api",
        "redundancy_group_min_healthy": 1,
        "availability": Decimal("0.99"),
    }
    unsure = tuple(comp("us", region="us-east-1", **loose) if n.id == "us" else n for n in nodes)
    [web] = [
        f
        for f in of(run(unsure, links), T.AVAILABILITY_NOT_EVALUABLE)
        if Evidence("entry", "web") in f.evidence
    ]
    assert {"us.failure_independence", "us.failover_mode"} <= set(web.missing)
    assert "us" in web.node_ids


def test_inferred_or_proposed_data_on_a_path_is_flagged() -> None:
    inferred = {"configuration.availability": Provenance(ProvenanceSource.CLOUD_DISCOVERY, inferred=True)}
    proposed = {
        "configuration.mttr_seconds": Provenance(ProvenanceSource.LLM_PROPOSAL, confidence=Decimal("0.6"))
    }
    confirmed = {"configuration.availability": Provenance(ProvenanceSource.TERRAFORM, verified=True)}
    nodes = (
        node("web", NodeKind.CLIENT),
        comp("api", availability=Decimal("0.999"), field_provenance=inferred),
        comp("db", NodeKind.DATABASE, mttr_seconds=60, field_provenance=proposed),
        comp("cache", NodeKind.CACHE, availability=Decimal("0.99"), field_provenance=confirmed),
        comp("batch", mttr_seconds=5, field_provenance=proposed),  # on no path
    )
    links = (call("web", "api"), call("api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql"),
             call("api", "cache", kind=ConnectionKind.DATA_ACCESS, protocol="redis"))  # fmt: skip
    flagged = {f.node_ids[0]: f.evidence for f in of(run(nodes, links), T.UNVERIFIED_RELIABILITY_DATA)}
    assert flagged == {
        "api": (Evidence("configuration.availability", "0.999 (cloud_discovery, inferred)"),),
        "db": (Evidence("configuration.mttr_seconds", "60 (llm_proposal)"),),
    }


# --- the quality every finding has ---------------------------------------------------------------


def test_every_finding_is_complete_and_actionable() -> None:
    result = run(*shop())
    assert {f.type for f in result.findings} >= {
        T.SINGLE_POINT_OF_FAILURE, T.CRITICAL_DEPENDENCY_WITHOUT_ALTERNATIVE, T.AVAILABILITY_NOT_EVALUABLE,
        T.MISSING_RECOVERY_DATA, T.UNMODELED_DEPENDENCY, T.NO_REDUNDANCY,
    }  # fmt: skip
    for finding in result.findings:
        assert finding.node_ids or finding.connection_ids, finding.id  # what it is about
        assert finding.evidence or finding.missing, finding.id  # its evidence, or what is missing
        assert len(finding.explanation) > 40, finding.id  # what was detected and why it matters
        assert finding.recommendation, finding.id  # an option for review
        assert finding.model_id is not None, finding.id  # who produced it
        text = f"{finding.explanation} {finding.recommendation}".lower()
        for claim in ("will fail", "guarantee", "certainly", "will cause", "risk score"):
            assert claim not in text, (finding.id, claim)


def test_findings_carry_no_score() -> None:
    for finding in run(*shop()).findings:
        assert not {"score", "risk", "probability", "confidence"} & set(finding.to_dict()), finding.id


def test_findings_are_reproducible() -> None:
    nodes, links = shop()
    first, second = run(nodes, links), run(tuple(reversed(nodes)), tuple(reversed(links)))
    assert [f.to_dict() for f in first.findings] == [f.to_dict() for f in second.findings]
    assert [f.severity for f in first.findings] == sorted(
        (f.severity for f in first.findings), key=list(Severity).index
    )
