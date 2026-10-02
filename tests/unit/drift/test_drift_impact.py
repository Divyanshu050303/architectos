"""Impact context and downstream analyses (Drift Detection Engine, phase 6): each finding names the
engines whose models read what it concerns — by component specification, or by a documented rule —
and links their stored analyses of the baseline revision (current, stale or missing); nothing is
recomputed, no impact claimed, and drift works without any downstream analysis."""

import json
import uuid
from datetime import UTC, datetime

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.serialization import content_hash
from core.architecture_ir.traceability import DecisionRef, RequirementRef
from core.domain.discovery.results import DiscoveryResult
from core.domain.discovery.runs import ArtifactInput, DiscoveryRequest
from core.domain.drift.analyses import BaselineRef, DriftRequest, DriftResult, ObservedRef
from core.domain.drift.findings import DriftFinding
from core.domain.drift.values import FindingType
from core.domain.evolution.evidence import EvidenceItem, StoredAnalysis
from core.domain.evolution.triggers import TriggerKind
from core.domain.evolution.values import EvidenceSource, EvidenceState
from core.domain.migrations.evidence import AnalysisEvidence
from engines.discovery.engine import DeterministicDiscoveryEngine
from engines.drift.compatibility import BaselineInput, ObservedInput
from engines.drift.engine import DeterministicDriftEngine
from persistence.component_catalog import default_catalog

CATALOG = default_catalog()
DISCOVERY = DeterministicDiscoveryEngine(CATALOG)
AT = datetime(2026, 10, 2, tzinfo=UTC)
REQUEST = DriftRequest(uuid.UUID(int=1), 1, uuid.UUID(int=2))
DB = "terraform:aws_db_instance.main"
SHOP = "name: shop\nservices:\n  db:\n    image: postgres:16\n"
S = EvidenceSource


def discover(*artifacts: tuple[str, str]) -> DiscoveryResult:
    return DISCOVERY.discover(DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts)))


def terraform(**attributes: object) -> str:
    return json.dumps({"resource": {"aws_db_instance": {"main": {"engine": "postgres", **attributes}}}})


def analyze(
    source: DiscoveryResult,
    later: DiscoveryResult,
    evidence: tuple[AnalysisEvidence, ...] = (),
    engine: DeterministicDriftEngine | None = None,
    ir: ArchitectureIR | None = None,
) -> DriftResult:
    baseline_ir = ir or source.proposed
    assert baseline_ir is not None
    ref = BaselineRef(uuid.UUID(int=1), 1, content_hash(baseline_ir), 1)
    observed = ObservedRef(
        uuid.UUID(int=2), later.fingerprint, later.sources_fingerprint, later.version, later.extractors
    )
    drift = engine or DeterministicDriftEngine(CATALOG)
    baseline = BaselineInput(ref, baseline_ir, AT, 1, source)
    return drift.analyze(REQUEST, baseline, ObservedInput(observed, later, AT), None, evidence)


def stored(source: EvidenceSource, revision: int, digest: str, *items: str) -> AnalysisEvidence:
    found = tuple(EvidenceItem(TriggerKind.FINDING, "x", DB, item) for item in items)
    return AnalysisEvidence(StoredAnalysis(source, uuid.uuid4(), revision, digest, "1", "completed", found))


def storage(result: DriftResult) -> DriftFinding:
    return next(f for f in result.findings if f.path == "configuration.storage_bytes")


def resized() -> tuple[DiscoveryResult, DiscoveryResult]:
    return (
        discover(("db.tf.json", terraform(allocated_storage=20))),
        discover(("db.tf.json", terraform(allocated_storage=40))),
    )


def test_a_property_names_the_engines_its_component_specification_lists() -> None:
    impact = {i.engine: i for i in storage(analyze(*resized())).impact}
    assert set(impact) == {S.CAPACITY, S.COST}
    assert impact[S.CAPACITY].basis.startswith(
        "databases/postgresql@2 lists capacity as reading storage_bytes."
    )
    assert {i.state for i in impact.values()} == {EvidenceState.MISSING}  # nothing stored: nothing claimed


def test_stored_analyses_of_the_baseline_are_linked_current_or_stale() -> None:
    source, later = resized()
    assert source.proposed is not None
    evidence = (stored(S.CAPACITY, 1, content_hash(source.proposed), "cap_db"), stored(S.COST, 3, "f" * 64))
    impact = {i.engine: i for i in storage(analyze(source, later, evidence)).impact}
    assert (impact[S.CAPACITY].state, impact[S.CAPACITY].items) == (EvidenceState.CURRENT, ("cap_db",))
    assert (impact[S.COST].state, impact[S.COST].revision_number) == (EvidenceState.STALE, 3)
    assert "not relied on" in impact[S.COST].basis


def test_structure_and_connections_name_the_engines_that_read_them() -> None:
    two = SHOP + "  ledger:\n    image: postgres:16\n"
    added = analyze(discover(("compose.yaml", SHOP)), discover(("compose.yaml", two)))
    ledger = next(f for f in added.findings if f.type is FindingType.COMPONENT_ADDED)
    assert [i.engine for i in ledger.impact] == [S.VALIDATION]
    db = "compose:shop/service/db"
    nodes = (Node(db, NodeKind.DATABASE, "db"), Node("api", NodeKind.SERVICE, "API"))
    written = ArchitectureIR(
        "Shop", nodes=nodes, connections=(Connection("c1", "api", db, ConnectionKind.DATA_ACCESS),)
    )
    removed = analyze(discover(("compose.yaml", SHOP)), discover(("compose.yaml", SHOP)), ir=written)
    connection = next(f for f in removed.findings if f.subject == "connection:c1")
    assert [i.engine for i in connection.impact] == [S.CAPACITY, S.RELIABILITY, S.SECURITY]


def test_a_property_no_specification_lists_claims_nothing_specific() -> None:
    source = discover(("db.tf.json", terraform(instance_class="db.t3.micro")))
    result = analyze(source, discover(("db.tf.json", terraform(instance_class="db.r6g.large"))))
    changed = next(f for f in result.findings if f.path == "configuration.instance_class")
    assert all("for some components" in i.basis for i in changed.impact)  # catalog-wide, stated as such


def test_drift_works_without_downstream_analyses_and_impact_is_not_identity() -> None:
    source, later = resized()
    plain = analyze(source, later, engine=DeterministicDriftEngine())
    assert storage(plain).impact == ()
    assert storage(plain).id == storage(analyze(source, later)).id  # context never changes identity
    assert analyze(source, later).fingerprint == analyze(source, later).fingerprint


def test_requirements_and_decisions_the_element_references_are_named_not_judged() -> None:
    requirement, decision = uuid.UUID(int=41), uuid.UUID(int=42)
    db = "compose:shop/service/db"
    nodes = (
        Node(db, NodeKind.DATABASE, "db"),
        Node("api", NodeKind.SERVICE, "API", requirement_refs=(RequirementRef(requirement, 2),)),
    )
    written = ArchitectureIR("Shop", nodes=nodes, decisions=(DecisionRef(decision, ("api",)),))
    result = analyze(discover(("compose.yaml", SHOP)), discover(("compose.yaml", SHOP)), ir=written)
    api = next(f for f in result.findings if f.subject == "node:api")
    assert api.references == (f"decision:{decision}", f"requirement:{requirement}@2")
    assert "violat" not in str(api.to_dict()).lower()
