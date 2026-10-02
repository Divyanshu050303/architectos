"""Comparison input compatibility (Drift Detection Engine, phase 2): every dimension decided before
any difference, deterministically and with an actionable explanation; parser, rule and schema
changes never read as architecture changes; partial coverage kept partial; missing coverage never a
removal; a baseline nothing identifies needs confirmed identity mappings."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from core.architecture_ir.component import NodeKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.serialization import content_hash
from core.domain.discovery.results import DiscoveryResult
from core.domain.discovery.runs import ArtifactInput, DiscoveryRequest
from core.domain.discovery.values import SourceType
from core.domain.drift.analyses import BaselineRef, ObservedRef
from core.domain.drift.values import Compatibility
from engines.discovery.engine import DeterministicDiscoveryEngine
from engines.drift.compatibility import Assessment, BaselineInput, ObservedInput, assess
from engines.drift.sources import discovered_from
from persistence.component_catalog import default_catalog
from tests.unit.discovery.test_discovery_sources import DEPLOYMENT

ENGINE = DeterministicDiscoveryEngine(default_catalog())
AT = datetime(2026, 10, 2, tzinfo=UTC)
C = Compatibility
SHOP = "name: shop\nservices:\n  db:\n    image: postgres:16\n"
BILLING = "name: billing\nservices:\n  ledger:\n    image: postgres:16\n"


def discover(*artifacts: tuple[str, str]) -> DiscoveryResult:
    return ENGINE.discover(DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts)))


def baseline(
    ir: ArchitectureIR, source: DiscoveryResult | None, *, schema: int = 1, latest: int = 1
) -> BaselineInput:
    return BaselineInput(BaselineRef(uuid.UUID(int=1), 1, content_hash(ir), schema), ir, AT, latest, source)


def observed(result: DiscoveryResult, at: datetime = AT) -> ObservedInput:
    ref = ObservedRef(
        uuid.UUID(int=2), result.fingerprint, result.sources_fingerprint, result.version, result.extractors
    )
    return ObservedInput(ref, result, at)


def accepted(result: DiscoveryResult) -> ArchitectureIR:
    assert result.proposed is not None
    return result.proposed


def outcomes(assessment: Assessment) -> dict[str, Compatibility]:
    return {c.dimension: c.outcome for c in assessment.checks}


def against(source: DiscoveryResult, later: DiscoveryResult) -> Assessment:
    return assess(baseline(accepted(source), source), observed(later))


def test_the_same_sources_read_alike_are_compatible() -> None:
    assessment = against(discover(("compose.yaml", SHOP)), discover(("compose.yaml", SHOP)))
    assert assessment.status is C.COMPATIBLE
    assert set(outcomes(assessment)) == {
        "ir_schema", "discovery_result", "extractor_versions", "source_types", "source_coverage",
        "artifact_coverage", "identity", "freshness",
    }  # fmt: skip
    assert assessment.coverage.inspected == ("compose.yaml",)
    assert assessment.warnings == ()


def test_a_changed_extractor_makes_its_source_type_not_comparable() -> None:
    source = discover(("compose.yaml", SHOP), ("k8s/shop.yaml", DEPLOYMENT))
    later = discover(("compose.yaml", SHOP), ("k8s/shop.yaml", DEPLOYMENT))
    changed = replace(later, extractors=dict(later.extractors) | {"docker_compose": 2})
    assessment = against(source, changed)
    assert outcomes(assessment)["extractor_versions"] is C.PARTIALLY_COMPARABLE
    assert assessment.not_comparable == frozenset({SourceType.DOCKER_COMPOSE})
    shop = discover(("compose.yaml", SHOP))
    only = replace(shop, extractors=dict(shop.extractors) | {"compose-kinds": 2})
    assert outcomes(against(shop, only))["extractor_versions"] is C.INCOMPATIBLE  # all it reads changed


def test_a_changed_shared_rule_makes_the_inputs_incompatible() -> None:
    later = discover(("compose.yaml", SHOP))
    changed = replace(later, extractors=dict(later.extractors) | {"discovery-catalog": 2})
    assessment = against(discover(("compose.yaml", SHOP)), changed)
    assert assessment.status is C.INCOMPATIBLE
    check = next(c for c in assessment.checks if c.dimension == "extractor_versions")
    assert "discovery-catalog 1 -> 2" in check.message  # actionable: what changed


def test_a_hand_written_baseline_needs_confirmed_identity() -> None:
    written = ArchitectureIR("Shop", nodes=(Node("db", NodeKind.DATABASE, "Database"),))
    run = observed(discover(("compose.yaml", SHOP)))
    found = outcomes(assess(baseline(written, None), run))
    assert found["extractor_versions"] is C.COMPATIBLE_WITH_WARNINGS  # the versions it rests on: unknown
    assert found["source_types"] is C.COMPATIBLE_WITH_WARNINGS  # no removal can be established
    assert found["identity"] is C.CANNOT_DETERMINE
    mapped = assess(baseline(written, None), run, {"db": "compose:shop/service/db"})
    assert outcomes(mapped)["identity"] is C.COMPATIBLE


def test_partial_and_missing_coverage_stay_partial() -> None:
    source = discover(("compose.yaml", SHOP), ("billing.yaml", BILLING))
    partly = discover(("compose.yaml", SHOP + "include: [x.yaml]\n"), ("billing.yaml", BILLING))
    assert outcomes(against(source, partly))["source_coverage"] is C.PARTIALLY_COMPARABLE
    missing = against(source, discover(("compose.yaml", SHOP)))
    assert outcomes(missing)["artifact_coverage"] is C.PARTIALLY_COMPARABLE
    assert missing.uninspected == frozenset({"billing.yaml"})  # absence there is not compared as removal


def test_nothing_read_or_no_shared_source_type_is_incompatible() -> None:
    source = discover(("compose.yaml", SHOP))
    unread = against(source, discover(("main.tf", "resource {}")))
    assert outcomes(unread)["source_coverage"] is C.INCOMPATIBLE
    other = against(source, discover(("k8s/shop.yaml", DEPLOYMENT)))
    assert outcomes(other)["source_types"] is C.INCOMPATIBLE


def test_schema_and_freshness() -> None:
    source = discover(("compose.yaml", SHOP))
    ir = accepted(source)
    newer = assess(baseline(ir, source, schema=2), observed(discover(("compose.yaml", SHOP))))
    assert outcomes(newer)["ir_schema"] is C.INCOMPATIBLE
    old_run = observed(discover(("compose.yaml", SHOP)), AT - timedelta(days=1))
    stale = assess(baseline(ir, source, latest=4), old_run)
    freshness = next(c for c in stale.checks if c.dimension == "freshness")
    assert freshness.outcome is C.COMPATIBLE_WITH_WARNINGS
    assert "Revision 4 is later" in freshness.message
    assert "older than the baseline" in freshness.message


def test_compatibility_is_deterministic_and_reads_recorded_provenance() -> None:
    source = discover(("compose.yaml", SHOP))
    run = discover(("compose.yaml", SHOP))
    assert against(source, run) == against(source, run)
    node = accepted(source).node("compose:shop/service/db")
    assert node is not None
    origin = discovered_from(node)
    assert origin is not None
    assert (origin.source_type, origin.artifact) == (SourceType.DOCKER_COMPOSE, "compose.yaml")
    assert discovered_from(Node("db", NodeKind.DATABASE, "Database")) is None  # written by a person
