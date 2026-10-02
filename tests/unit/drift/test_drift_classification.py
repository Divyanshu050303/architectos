"""Coverage-aware drift classification (Drift Detection Engine, phase 5), end to end through the
engine: confirmed and potential differences kept apart; a removal confirmed only where the scope was
inspected; missing coverage never a removal; incompatible inputs and changed parsers never compared;
"no difference" said only of the inspected scope; deterministic."""

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from core.architecture_ir.component import NodeKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.serialization import content_hash
from core.domain.discovery.results import DiscoveryResult
from core.domain.discovery.runs import ArtifactInput, DiscoveryRequest
from core.domain.drift.analyses import BaselineRef, DriftAnalysis, DriftRequest, DriftResult, ObservedRef
from core.domain.drift.findings import DriftFinding
from core.domain.drift.values import AnalysisStatus, Classification, FindingType
from engines.discovery.engine import DeterministicDiscoveryEngine
from engines.drift.compatibility import BaselineInput, ObservedInput
from engines.drift.engine import DeterministicDriftEngine
from persistence.component_catalog import default_catalog

DISCOVERY = DeterministicDiscoveryEngine(default_catalog())
DRIFT = DeterministicDriftEngine()
AT = datetime(2026, 10, 2, tzinfo=UTC)
C, F = Classification, FindingType
SHOP = "name: shop\nservices:\n  db:\n    image: postgres:16\n"
TWO = SHOP + "  ledger:\n    image: postgres:16\n"
BILLING = "name: billing\nservices:\n  ledger:\n    image: postgres:16\n"
PARTLY = "name: billing\nservices: {}\ninclude: [x.yaml]\n"
QUEUE = '{"resource": {"aws_sqs_queue": {"q": {"name": "q"}}}}'
REQUEST = DriftRequest(uuid.UUID(int=1), 1, uuid.UUID(int=2))
LEDGER, BILLING_LEDGER = "node:compose:shop/service/ledger", "node:compose:billing/service/ledger"
DB = "node:compose:shop/service/db"
TF_DB = "node:terraform:aws_db_instance.main"


def discover(*artifacts: tuple[str, str]) -> DiscoveryResult:
    return DISCOVERY.discover(DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts)))


def terraform(**attributes: object) -> str:
    return json.dumps({"resource": {"aws_db_instance": {"main": {"engine": "postgres", **attributes}}}})


def analyze(
    later: DiscoveryResult,
    source: DiscoveryResult | None = None,
    ir: ArchitectureIR | None = None,
    mappings: dict[str, str] | None = None,
) -> DriftResult:
    baseline_ir = ir if ir is not None else (source.proposed if source else None)
    assert baseline_ir is not None
    ref = BaselineRef(uuid.UUID(int=1), 1, content_hash(baseline_ir), 1)
    observed = ObservedRef(
        uuid.UUID(int=2), later.fingerprint, later.sources_fingerprint, later.version, later.extractors
    )
    baseline = BaselineInput(ref, baseline_ir, AT, 1, source)
    return DRIFT.analyze(REQUEST, baseline, ObservedInput(observed, later, AT), mappings)


def finding(result: DriftResult, subject: str, path: str | None = None) -> DriftFinding:
    return next(f for f in result.findings if f.subject == subject and f.path == path)


def status(result: DriftResult) -> AnalysisStatus:
    analysis = DriftAnalysis(
        uuid.UUID(int=9), uuid.UUID(int=8), REQUEST, AnalysisStatus.PENDING, uuid.UUID(int=3), AT
    )
    return analysis.start(AT).finish(result, AT).status


def test_no_difference_is_stated_only_of_the_inspected_scope() -> None:
    result = analyze(discover(("compose.yaml", SHOP)), discover(("compose.yaml", SHOP)))
    assert (result.findings, result.no_difference_within_coverage) == ((), True)
    assert result.coverage.inspected == ("compose.yaml",)
    assert status(result) is AnalysisStatus.COMPLETED


def test_a_confirmed_addition() -> None:
    added = finding(analyze(discover(("compose.yaml", TWO)), discover(("compose.yaml", SHOP))), LEDGER)
    assert (added.type, added.classification) == (F.COMPONENT_ADDED, C.CONFIRMED)
    assert added.evidence


def test_a_confirmed_removal_where_the_scope_was_inspected() -> None:
    removed = finding(analyze(discover(("compose.yaml", SHOP)), discover(("compose.yaml", TWO))), LEDGER)
    assert (removed.type, removed.classification) == (F.COMPONENT_REMOVED, C.CONFIRMED)
    assert removed.baseline_reference == "compose.yaml#0:services.ledger"


def test_missing_coverage_is_never_a_removal() -> None:
    source = discover(("compose.yaml", SHOP), ("billing.yaml", BILLING))
    unread = analyze(discover(("compose.yaml", SHOP)), source)
    ledger = finding(unread, BILLING_LEDGER)
    assert (ledger.type, ledger.classification) == (F.COMPONENT_REMOVED, C.UNKNOWN)
    coverage = finding(unread, "artifact:billing.yaml")
    assert (coverage.type, coverage.classification) == (F.COVERAGE_CHANGED, C.UNKNOWN)
    partly = analyze(discover(("compose.yaml", SHOP), ("billing.yaml", PARTLY)), source)
    assert finding(partly, BILLING_LEDGER).classification is C.POTENTIAL
    assert status(partly) is AnalysisStatus.COMPLETED_WITH_WARNINGS


def test_a_configuration_change_is_confirmed_with_source_evidence() -> None:
    source = discover(("db.tf.json", terraform(instance_class="db.t3.micro")))
    result = analyze(discover(("db.tf.json", terraform(instance_class="db.r6g.large"))), source)
    changed = finding(result, TF_DB, "configuration.instance_class")
    assert (changed.classification, changed.baseline_value, changed.discovered_value) == (
        C.CONFIRMED, "db.t3.micro", "db.r6g.large",
    )  # fmt: skip
    assert changed.locations == ("db.tf.json#0:resource.aws_db_instance.main",)
    gone = analyze(discover(("db.tf.json", terraform())), source)
    assert finding(gone, TF_DB, "configuration.instance_class").classification is C.POTENTIAL


def test_inferences_and_hand_written_baselines_stay_uncertain() -> None:
    nodes = (
        Node("compose:shop/service/db", NodeKind.CACHE, "db"),
        Node("legacy", NodeKind.SERVICE, "Legacy"),
    )
    result = analyze(discover(("compose.yaml", SHOP)), ir=ArchitectureIR("Shop", nodes=nodes))
    assert finding(result, DB, "kind").classification is C.POTENTIAL  # a kind the catalog implies
    legacy = finding(result, "node:legacy")
    assert (legacy.type, legacy.classification) == (F.COMPONENT_REMOVED, C.UNKNOWN)  # never discovered


def test_incompatible_inputs_and_changed_parsers_are_not_compared() -> None:
    later = discover(("compose.yaml", TWO))
    rules = replace(later, extractors=dict(later.extractors) | {"discovery-catalog": 2})
    blocked = analyze(rules, discover(("compose.yaml", SHOP)))
    assert {f.type for f in blocked.findings} == {F.COMPARISON_INCOMPATIBLE}
    assert {f.classification for f in blocked.findings} == {C.NOT_COMPARABLE}
    assert status(blocked) is AnalysisStatus.INCOMPATIBLE_INPUTS
    source = discover(("compose.yaml", SHOP), ("queue.tf.json", QUEUE))
    mixed = discover(("compose.yaml", TWO), ("queue.tf.json", QUEUE))
    parser = replace(mixed, extractors=dict(mixed.extractors) | {"docker_compose": 2})
    assert finding(analyze(parser, source), LEDGER).classification is C.NOT_COMPARABLE


def test_ambiguous_identity_is_unknown_with_its_candidates() -> None:
    nodes = (
        Node("compose:shop/service/db", NodeKind.DATABASE, "db"),
        Node("old-db", NodeKind.DATABASE, "Old"),
    )
    result = analyze(
        discover(("compose.yaml", SHOP)),
        ir=ArchitectureIR("Shop", nodes=nodes),
        mappings={"old-db": "compose:shop/service/db"},
    )
    unresolved = finding(result, "node:old-db")
    assert (unresolved.type, unresolved.classification) == (F.UNRESOLVED_DIFFERENCE, C.UNKNOWN)
    assert any("Candidates: compose:shop/service/db" in limitation for limitation in unresolved.limitations)


def test_classification_is_deterministic() -> None:
    source = discover(("compose.yaml", SHOP), ("billing.yaml", BILLING))
    first = analyze(discover(("compose.yaml", TWO)), source)
    again = analyze(discover(("compose.yaml", TWO)), source)
    assert first.fingerprint == again.fingerprint
    assert [f.id for f in first.findings] == [f.id for f in again.findings]
