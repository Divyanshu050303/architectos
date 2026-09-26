"""End-to-end availability (Milestone 9, phase 5): series and declared alternatives on request paths,
composed only where the architecture makes them explicit, with the analyzed scope stated."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.results import Source
from core.domain.capacity.units import Quantity
from core.domain.engine_results import Evidence
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.reliability.results import PathResult, ReliabilityResult, ReliabilityStatus
from core.domain.validation.options import RevisionInfo
from engines.reliability.context import ReliabilityContext
from engines.reliability.engine import analyze
from engines.reliability.failure_propagation import at_least
from engines.reliability.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
SYNC: dict[str, Any] = {
    "kind": ConnectionKind.REQUEST,
    "protocol": "https",
    "interaction": Interaction.SYNCHRONOUS,
}
MEMBER: dict[str, Any] = {
    "redundancy_group": "api", "redundancy_group_min_healthy": 1,
    "failure_independence": "independent", "failover_mode": "automatic",
}  # fmt: skip


def comp(node_id: str, available: str | None = None, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Any:
    declared = {"availability": Decimal(available)} if available else {}
    return node(node_id, kind, configuration=Configuration(declared | values))


def call(source: str, target: str) -> Any:
    return connection(f"{source}-{target}", source, target, **SYNC)


def run(nodes: tuple[Any, ...], links: tuple[Any, ...], **request: Any) -> ReliabilityResult:
    ir = ArchitectureIR("Shop", nodes=nodes, connections=links)
    context = ReliabilityContext(ir, REVISION, ReliabilityAnalysisRequest(uuid.UUID(int=1), 1, **request))
    return analyze(context, default_registry())


def path(result: ReliabilityResult, entry: str = "web") -> PathResult:
    return next(p for p in result.paths if p.entry_id == entry)


def regions(
    eu: dict[str, Any] | None = None, us: dict[str, Any] | None = None, **shared: Any
) -> tuple[Any, ...]:
    """web -> lb -> {eu, us} (a redundancy group) -> their own databases."""
    nodes = (
        node("web", NodeKind.CLIENT), comp("lb", "0.9999", NodeKind.LOAD_BALANCER),
        comp("eu", "0.99", **(MEMBER | (eu or {}))), comp("us", "0.99", **(MEMBER | (us or {}))),
        comp("db-eu", "0.99", NodeKind.DATABASE), comp("db-us", "0.99", NodeKind.DATABASE),
    )  # fmt: skip
    links = (call("web", "lb"), call("lb", "eu"), call("lb", "us"), call("eu", "db-eu"), call("us", "db-us"))
    return nodes, links


def test_at_least_k_of_n_for_different_probabilities() -> None:
    assert at_least(1, [Decimal("0.9"), Decimal("0.9")]) == Decimal("0.99")
    assert at_least(1, [Decimal("0.99"), Decimal("0.9")]) == Decimal("0.999")
    assert at_least(2, [Decimal("0.99"), Decimal("0.9")]) == Decimal("0.891")
    assert at_least(0, [Decimal("0.5")]) == 1
    assert at_least(3, [Decimal("0.5"), Decimal("0.5")]) == 0


def test_declared_alternatives_with_their_own_dependencies_are_parallel() -> None:
    result = run(*regions())
    estimate = path(result).availability
    # branches: 0.99 x 0.99 = 0.9801 each; 1 of 2: 1 - 0.0199^2 = 0.99960399; x lb 0.9999
    assert estimate.quantity == Quantity.of("0.999504030", "ratio")
    assert estimate.source is Source.MODEL_ESTIMATE
    assert Evidence("redundancy_group.api", "1 of 2: eu (0.9801), us (0.9801)") in estimate.inputs
    assumptions = [e.value for e in estimate.inputs if e.label == "assumption"]
    assert any("declared, not verified" in a for a in assumptions)
    assert any("independent" in a for a in assumptions)
    assert path(result).node_ids == ("web", "lb", "eu", "us", "db-eu", "db-us")  # the analyzed scope


def test_what_every_alternative_needs_stays_in_series() -> None:
    nodes = (
        node("web", NodeKind.CLIENT), comp("lb", "0.9999", NodeKind.LOAD_BALANCER),
        comp("eu", "0.99", **MEMBER), comp("us", "0.99", **MEMBER), comp("db", "0.999", NodeKind.DATABASE),
    )  # fmt: skip
    links = (call("web", "lb"), call("lb", "eu"), call("lb", "us"), call("eu", "db"), call("us", "db"))
    expected = Decimal("0.9999") * (1 - Decimal("0.01") ** 2) * Decimal("0.999")
    assert path(run(nodes, links)).availability.quantity == Quantity.rounded(expected, "ratio")


def test_every_member_must_declare_independence_automatic_failover_and_one_minimum() -> None:
    for eu, us, missing in (
        ({"failure_independence": "unknown"}, {}, ("eu.failure_independence",)),
        ({}, {"failover_mode": "manual"}, ("us.failover_mode",)),
        ({"redundancy_group_min_healthy": 2}, {}, ("consistent.redundancy_group.api.min_healthy",)),
        (
            {"redundancy_group_min_healthy": 3},
            {"redundancy_group_min_healthy": 3},
            ("consistent.redundancy_group.api.min_healthy",),
        ),
    ):
        estimate = path(run(*regions(eu, us))).availability
        assert (estimate.quantity, estimate.missing) == (None, missing), (eu, us)


def test_both_alternatives_needed_is_their_product() -> None:
    both = {"redundancy_group_min_healthy": 2}
    estimate = path(run(*regions(both, both))).availability
    assert estimate.quantity == Quantity.rounded(Decimal("0.9999") * Decimal("0.9801") ** 2, "ratio")


def test_alternatives_that_overlap_in_part_are_not_composed() -> None:
    nodes = (
        node("web", NodeKind.CLIENT), comp("a", "0.99", **MEMBER), comp("b", "0.99", **MEMBER),
        comp("c", "0.99", **MEMBER), comp("x", "0.99", NodeKind.DATABASE),
    )  # fmt: skip
    links = (call("web", "a"), call("web", "b"), call("web", "c"), call("a", "x"), call("b", "x"))
    estimate = path(run(nodes, links)).availability
    assert (estimate.quantity, estimate.missing) == (None, ("disjoint.redundancy_group.api",))


def test_a_group_nested_in_another_groups_branch_is_not_composed() -> None:
    inner = MEMBER | {"redundancy_group": "cache"}
    nodes = (
        node("web", NodeKind.CLIENT), comp("eu", "0.99", **MEMBER), comp("us", "0.99", **MEMBER),
        comp("c1", "0.9", NodeKind.CACHE, **inner), comp("c2", "0.9", NodeKind.CACHE, **inner),
    )  # fmt: skip
    links = (call("web", "eu"), call("web", "us"), call("eu", "c1"), call("eu", "c2"))
    estimate = path(run(nodes, links)).availability
    assert estimate.quantity is None
    assert "unnested.redundancy_group.api" in estimate.missing


def test_a_single_routed_member_is_in_series() -> None:
    nodes, links = regions()
    routed = tuple(link for link in links if link.id not in {"lb-us", "us-db-us"})
    estimate = path(run(nodes, routed)).availability
    assert estimate.quantity == Quantity.rounded(
        Decimal("0.9999") * Decimal("0.99") * Decimal("0.99"), "ratio"
    )
    assert all(e.label != "redundancy_group.api" for e in estimate.inputs)


def test_each_entry_has_its_own_path_and_there_is_no_architecture_wide_figure() -> None:
    nodes = (
        node("web", NodeKind.CLIENT), node("partner", NodeKind.CLIENT),
        comp("api", "0.999"), comp("batch"), comp("db", "0.9995", NodeKind.DATABASE),
    )  # fmt: skip
    links = (call("web", "api"), call("api", "db"), call("partner", "batch"), call("batch", "db"))
    result = run(nodes, links)
    web, partner = path(result), path(result, "partner")
    assert web.availability.quantity == Quantity.rounded(Decimal("0.999") * Decimal("0.9995"), "ratio")
    assert (partner.complete, partner.availability.missing) == (False, ("batch.availability",))
    summary = result.summary()
    assert (summary["paths"], summary["paths_estimated"]) == (2, 1)  # partial coverage, stated
    assert result.status is ReliabilityStatus.PARTIAL
    document = result.to_dict()
    assert "availability" not in document
    assert "availability" not in document["summary"]


@pytest.mark.parametrize("entries", [("web",), None])
def test_paths_are_deterministic(entries: tuple[str, ...] | None) -> None:
    nodes, links = regions()
    first = run(nodes, links, entries=entries)
    second = run(tuple(reversed(nodes)), tuple(reversed(links)), entries=entries)
    assert first.to_dict() == second.to_dict()
