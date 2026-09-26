"""Hardening (Milestone 9, phase 9): the largest architectures complete within the result contract,
exact numbers at the edges, and determinism under repetition and reordering."""

import math
import uuid
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.units import Quantity
from core.domain.reliability.analyses import Objective, ReliabilityAnalysisRequest
from core.domain.reliability.results import FindingType, ObjectiveKind, ReliabilityResult, ReliabilityStatus
from core.domain.validation.options import RevisionInfo
from engines.reliability.availability import k_of_n
from engines.reliability.engine import names
from engines.reliability.failure_propagation import at_least
from engines.reliability.service import DeterministicReliabilityEngine
from tests.unit.architecture_ir.builders import connection, node

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
SYNC: dict[str, Any] = {
    "kind": ConnectionKind.REQUEST,
    "protocol": "https",
    "interaction": Interaction.SYNCHRONOUS,
}
OBJECTIVES = (
    Objective("slo", ObjectiveKind.AVAILABILITY, target=Decimal("0.9")),
    Objective("n", ObjectiveKind.REDUNDANCY, target=Decimal(2)),
    Objective("rto", ObjectiveKind.RECOVERY_TIME, duration=Quantity.of("1", "h")),
)


def call(source: str, target: str) -> Any:
    return connection(f"{source}-{target}", source, target, **SYNC)


def run(nodes: list[Any], links: list[Any]) -> ReliabilityResult:
    ir = ArchitectureIR("Big", nodes=tuple(nodes), connections=tuple(links))
    request = ReliabilityAnalysisRequest(uuid.UUID(int=1), 1, objectives=OBJECTIVES)
    return DeterministicReliabilityEngine().analyze(ir, REVISION, request, ())


def chain(length: int, **values: Any) -> tuple[list[Any], list[Any]]:
    base: dict[str, Any] = {"availability": Decimal("0.9999"), "mttr_seconds": 60}
    config = Configuration(base | values)
    nodes = [node("web", NodeKind.CLIENT)] + [node(f"n{i:03d}", configuration=config) for i in range(length)]
    links = [call("web", "n000")] + [call(f"n{i:03d}", f"n{i + 1:03d}") for i in range(length - 1)]
    return nodes, links


# --- the largest architectures -------------------------------------------------------------------


def test_a_chain_through_every_node_completes() -> None:
    result = run(*chain(999))
    assert (result.status, result.unsupported) == (ReliabilityStatus.COMPLETED, ())
    [path] = result.paths
    assert len(path.node_ids) == 1000
    assert path.availability.quantity == Quantity.rounded(Decimal("0.9999") ** 999, "ratio")
    assert [e.label for e in path.availability.inputs][-1] == "series"  # hundreds of values folded, all shown
    folded = path.availability.inputs[-1].value
    assert folded.startswith("n000 0.9999, n001 0.9999")
    assert folded.endswith("and 899 more")  # bounded; every value is in the components


def test_a_cycle_through_every_node_completes_and_is_named_briefly() -> None:
    nodes, links = chain(998)
    result = run(nodes, [*links, call("n997", "n000")])
    assert (result.status, result.unsupported) == (ReliabilityStatus.COMPLETED, ())
    [cycle] = [f for f in result.findings if f.type is FindingType.CIRCULAR_DEPENDENCY]
    assert len(cycle.node_ids) == 998  # the finding names every element
    assert "and 978 more" in cycle.title  # its sentence names the first ones


def test_many_entries_and_a_large_group_complete() -> None:
    clients = [node(f"w{i:03d}", NodeKind.CLIENT) for i in range(500)]
    rest, links = chain(499)
    many = run(clients + rest[1:], [call(f"w{i:03d}", "n000") for i in range(500)] + links[1:])
    assert (many.status, many.unsupported, len(many.paths)) == (ReliabilityStatus.COMPLETED, (), 500)
    member: dict[str, Any] = {
        "availability": Decimal("0.99"),
        "redundancy_group": "g",
        "redundancy_group_min_healthy": 1,
        "failure_independence": "independent",
        "failover_mode": "automatic",
    }
    nodes = [
        node("web", NodeKind.CLIENT),
        node("lb", NodeKind.LOAD_BALANCER, configuration=Configuration({"availability": Decimal("0.9999")})),
    ]
    nodes += [node(f"m{i:03d}", configuration=Configuration(member)) for i in range(998)]
    group = run(nodes, [call("web", "lb")] + [call("lb", f"m{i:03d}") for i in range(998)])
    assert (group.status, group.unsupported) == (ReliabilityStatus.COMPLETED, ())
    assert group.paths[0].availability.quantity == Quantity.of("0.9999", "ratio")  # 1 - 0.01^998 rounds to 1


def test_unknown_inputs_on_a_long_path_are_bounded_in_the_estimate_and_listed_in_the_finding() -> None:
    nodes = [node("web", NodeKind.CLIENT)] + [node(f"n{i:03d}") for i in range(500)]
    links = [call("web", "n000")] + [call(f"n{i:03d}", f"n{i + 1:03d}") for i in range(499)]
    result = run(nodes, links)
    [path] = result.paths
    assert len(path.availability.missing) == 200
    assert "more.301_inputs" in path.availability.missing
    [finding] = [f for f in result.findings if f.type is FindingType.AVAILABILITY_NOT_EVALUABLE]
    assert len(finding.missing) == 500


def test_sentences_name_a_bounded_number_of_elements() -> None:
    assert names(["a", "b"]) == "a, b"
    assert names([f"n{i}" for i in range(25)]) == ", ".join(f"n{i}" for i in range(20)) + " and 5 more"


def test_many_entries_reaching_one_large_group_compose_it_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review finding: the group's composition was redone for every entry (22 s for this shape)."""
    import engines.reliability.failure_propagation as propagation  # noqa: PLC0415 - patched here

    calls: list[int] = []
    real = propagation.at_least

    def counting(k: int, probabilities: list[Decimal]) -> Decimal:
        calls.append(len(probabilities))
        return real(k, probabilities)

    monkeypatch.setattr(propagation, "at_least", counting)
    member: dict[str, Any] = {
        "availability": Decimal("0.99"),
        "redundancy_group": "g",
        "redundancy_group_min_healthy": 1,
        "failure_independence": "independent",
        "failover_mode": "automatic",
    }
    nodes = [node(f"w{i:03d}", NodeKind.CLIENT) for i in range(300)]
    nodes += [node("gw", NodeKind.GATEWAY, configuration=Configuration({"availability": Decimal("0.9999")}))]
    nodes += [node(f"m{i:03d}", configuration=Configuration(member)) for i in range(300)]
    links = [call(f"w{i:03d}", "gw") for i in range(300)] + [call("gw", f"m{i:03d}") for i in range(300)]
    result = run(nodes, links)
    assert (result.status, len(result.paths)) == (ReliabilityStatus.COMPLETED, 300)
    assert calls == [300]  # once for all 300 entries


def test_a_node_id_with_dots_is_named_whole() -> None:
    """Review finding: missing inputs were split at the first dot, blaming the wrong element."""
    nodes = [
        node("web", NodeKind.CLIENT),
        node("svc.api", configuration=Configuration({"availability": Decimal("0.99")})),
        node("db.primary", NodeKind.DATABASE),
    ]
    result = run(nodes, [call("web", "svc.api"), call("svc.api", "db.primary")])
    [finding] = [f for f in result.findings if f.type is FindingType.AVAILABILITY_NOT_EVALUABLE]
    assert (finding.node_ids, finding.missing) == (("db.primary",), ("db.primary.availability",))


# --- numbers at the edges ------------------------------------------------------------------------


def test_k_of_n_is_exact() -> None:
    a = Decimal("0.987")
    exact = sum(
        Fraction(math.comb(7, i)) * Fraction(a) ** i * (1 - Fraction(a)) ** (7 - i) for i in range(4, 8)
    )
    assert Fraction(k_of_n(a, 7, 4)) == exact
    assert at_least(2, [Decimal("0.9"), Decimal("0.8"), Decimal("0.7")]) == Decimal("0.902")  # 0.504 + 0.398


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"replica_availability": Decimal(1), "replicas": 1}, "1"),
        ({"replica_availability": Decimal(0), "replicas": 1}, "0"),
        ({"mtbf_seconds": Decimal("1e14"), "mttr_seconds": Decimal("0.001"), "replicas": 1}, "1"),
        ({"mtbf_seconds": Decimal("0.001"), "mttr_seconds": Decimal("1e14"), "replicas": 1}, "0"),
        (
            {
                "replica_availability": Decimal("0.5"),
                "replicas": 1000,
                "min_healthy_replicas": 1,
                "failure_independence": "independent",
                "failover_mode": "automatic",
            },
            "1",
        ),
    ],
)
def test_availability_at_the_edges(values: dict[str, Any], expected: str) -> None:
    result = run(
        [node("web", NodeKind.CLIENT), node("api", configuration=Configuration(values))], [call("web", "api")]
    )
    estimate = result.components[0].estimate("availability")
    assert estimate is not None
    assert estimate.quantity == Quantity.of(expected, "ratio")


def test_perfect_replicas_combine_to_one() -> None:
    """Review finding: 0 ** 0 raised when every replica is perfectly available (a = 1, n > 1)."""
    assert k_of_n(Decimal(1), 3, 2) == 1
    assert k_of_n(Decimal(0), 3, 2) == 0
    for values in (
        {"replica_availability": Decimal(1)},
        {"mtbf_seconds": 3600, "mttr_seconds": 0},  # a repair time of 0 s gives the same a = 1
    ):
        config: dict[str, Any] = {
            "replicas": 3,
            "min_healthy_replicas": 2,
            "failure_independence": "independent",
            "failover_mode": "automatic",
        } | values
        result = run(
            [node("web", NodeKind.CLIENT), node("api", configuration=Configuration(config))],
            [call("web", "api")],
        )
        estimate = result.components[0].estimate("availability")
        assert estimate is not None
        assert (estimate.quantity, result.unsupported) == (Quantity.of("1", "ratio"), ()), values


def test_beyond_a_thousand_replicas_is_not_calculated() -> None:
    values: dict[str, Any] = {
        "replica_availability": Decimal("0.9"),
        "replicas": 1001,
        "min_healthy_replicas": 1,
        "failure_independence": "independent",
        "failover_mode": "automatic",
    }
    result = run(
        [node("web", NodeKind.CLIENT), node("api", configuration=Configuration(values))], [call("web", "api")]
    )
    estimate = result.components[0].estimate("availability")
    assert estimate is not None
    assert (estimate.quantity, estimate.missing) == (None, ("magnitude",))


# --- determinism ---------------------------------------------------------------------------------


def test_repeated_and_reordered_runs_are_identical() -> None:
    nodes, links = chain(300)
    first, again = run(nodes, links), run(nodes, links)
    reordered = run(list(reversed(nodes)), list(reversed(links)))
    assert first.to_dict() == again.to_dict() == reordered.to_dict()
    assert [f.id for f in first.findings] == [f.id for f in reordered.findings]
