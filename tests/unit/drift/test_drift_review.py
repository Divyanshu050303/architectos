"""Finding review and resolution (Drift Detection Engine, phase 7): findings correlated across analyses
into drift items by a stable key; history never erased; a resolved difference detected again
reopens; an analysis resolves an item only when it compared, inspected the item's artifacts and no
longer detects it; review never changes the architecture."""

import itertools
import uuid
from datetime import UTC, datetime

from core.architecture_ir.serialization import content_hash
from core.domain.discovery.results import DiscoveryResult
from core.domain.discovery.runs import ArtifactInput, DiscoveryRequest
from core.domain.drift.analyses import BaselineRef, DriftAnalysis, DriftRequest, ObservedRef
from core.domain.drift.items import DriftItem
from core.domain.drift.review import Correlation, correlate, resolution_problem
from core.domain.drift.values import AnalysisStatus, ElementType, ReviewAction, ReviewStatus
from engines.discovery.engine import DeterministicDiscoveryEngine
from engines.drift.compatibility import BaselineInput, ObservedInput
from engines.drift.engine import DeterministicDriftEngine
from persistence.component_catalog import default_catalog

DISCOVERY = DeterministicDiscoveryEngine(default_catalog())
DRIFT = DeterministicDriftEngine()
AT = datetime(2026, 10, 2, tzinfo=UTC)
ARCH, PROJECT, ADA = uuid.UUID(int=1), uuid.UUID(int=8), uuid.UUID(int=3)
SHOP = "name: shop\nservices:\n  db:\n    image: postgres:16\n"
TWO = SHOP + "  ledger:\n    image: postgres:16\n"
BILLING = "name: billing\nservices:\n  ledger:\n    image: postgres:16\n"
LEDGER = "node:compose:shop/service/ledger"
_ids = itertools.count(100)


def discover(*artifacts: tuple[str, str]) -> DiscoveryResult:
    return DISCOVERY.discover(DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts)))


SOURCE = discover(("compose.yaml", SHOP))


def analysis(
    later: DiscoveryResult, source: DiscoveryResult = SOURCE, architecture: uuid.UUID = ARCH
) -> DriftAnalysis:
    assert source.proposed is not None
    request = DriftRequest(architecture, 1, uuid.UUID(int=2))
    ref = BaselineRef(architecture, 1, content_hash(source.proposed), 1)
    observed = ObservedRef(
        uuid.UUID(int=2), later.fingerprint, later.sources_fingerprint, later.version, later.extractors
    )
    baseline = BaselineInput(ref, source.proposed, AT, 1, source)
    result = DRIFT.analyze(request, baseline, ObservedInput(observed, later, AT))
    pending = DriftAnalysis(uuid.UUID(int=next(_ids)), PROJECT, request, AnalysisStatus.PENDING, ADA, AT)
    return pending.start(AT).finish(result, AT)


def items_after(*analyses: DriftAnalysis, start: tuple[DriftItem, ...] = ()) -> dict[str, DriftItem]:
    items = {i.key: i for i in start}
    for found in analyses:
        correlation = correlate(items.values(), found, AT, lambda: uuid.UUID(int=next(_ids)))
        items |= {i.key: i for i in (*correlation.created, *correlation.updated)}
    return items


def by_subject(items: dict[str, DriftItem]) -> dict[str, DriftItem]:
    return {i.subject: i for i in items.values()}


def test_an_analysis_opens_items_for_reviewable_differences_only() -> None:
    first = analysis(
        discover(("compose.yaml", TWO)), discover(("compose.yaml", SHOP), ("billing.yaml", BILLING))
    )
    assert first.result is not None
    assert "artifact:billing.yaml" in {f.subject for f in first.result.findings}  # stays with its analysis
    opened = by_subject(items_after(first))
    assert all(i.element is not ElementType.SCOPE for i in opened.values())
    assert (opened[LEDGER].status, opened[LEDGER].artifacts) == (ReviewStatus.OPEN, ("compose.yaml",))


def test_repeated_findings_correlate_without_losing_history() -> None:
    first, second = analysis(discover(("compose.yaml", TWO))), analysis(discover(("compose.yaml", TWO)))
    items = items_after(first)
    again = items_after(second, start=tuple(items.values()))
    assert set(again) == set(items)  # the same items: correlated, not duplicated
    ledger = by_subject(again)[LEDGER]
    assert (ledger.first_analysis_id, ledger.last_analysis_id) == (first.id, second.id)
    assert [e.analysis_id for e in ledger.history] == [first.id, second.id]
    assert correlate(again.values(), second, AT) == Correlation()  # the same analysis twice: nothing new
    later = items_after(analysis(discover(("compose.yaml", SHOP))), start=tuple(again.values()))
    assert by_subject(later)[LEDGER] == ledger  # not detected again: kept as it was, never erased


def test_a_resolved_difference_detected_again_reopens() -> None:
    later = analysis(discover(("compose.yaml", SHOP)))
    ledger = by_subject(items_after(analysis(discover(("compose.yaml", TWO)))))[LEDGER]
    assert resolution_problem(ledger, later) is None
    resolved = ledger.act(ReviewAction.RESOLVE, ADA, AT, evidence_analysis_id=later.id)
    again = by_subject(items_after(analysis(discover(("compose.yaml", TWO))), start=(resolved,)))[LEDGER]
    assert again.status is ReviewStatus.REOPENED
    assert [e.action for e in again.history][-2:] == [ReviewAction.RESOLVE, ReviewAction.DETECTED]


def test_only_a_comparable_inspecting_analysis_can_resolve() -> None:
    ledger = by_subject(items_after(analysis(discover(("compose.yaml", TWO)))))[LEDGER]
    assert resolution_problem(ledger, analysis(discover(("compose.yaml", TWO)))) == "still_detected"
    other = analysis(discover(("other.yaml", SHOP.replace("shop", "other"))))
    assert resolution_problem(ledger, other) == "outside_coverage"
    elsewhere = analysis(discover(("compose.yaml", SHOP)), architecture=uuid.UUID(int=99))
    assert resolution_problem(ledger, elsewhere) == "another_architecture"
    assert resolution_problem(ledger, analysis(discover(("main.tf", "resource {}")))) == "not_compared"


def test_review_never_changes_the_baseline() -> None:
    assert SOURCE.proposed is not None
    before = content_hash(SOURCE.proposed)
    ledger = by_subject(items_after(analysis(discover(("compose.yaml", TWO)))))[LEDGER]
    reviewed = ledger.act(ReviewAction.ACKNOWLEDGE, ADA, AT).act(ReviewAction.ACCEPT, ADA, AT)
    assert reviewed.status is ReviewStatus.ACCEPTED
    assert content_hash(SOURCE.proposed) == before
