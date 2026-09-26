"""Workload and capacity integration (Milestone 8, phase 5): usage-priced costs come from a stored
capacity analysis of the same revision (here: the real capacity engine's output), never recomputed,
never guessed, and a mismatched analysis is refused."""

import dataclasses
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.analyses import AnalysisReport, AnalysisRequest, CapacityAnalysis
from core.domain.capacity.results import AnalysisStatus
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.cost.analyses import CostAnalysisRequest
from core.domain.cost.capacity import CapacityBasis
from core.domain.cost.errors import IncompatibleCapacityAnalysis, InvalidCostRequest
from core.domain.cost.money import Money
from core.domain.cost.pricing import PricingSnapshot, PricingUnit
from core.domain.cost.results import CostKind, CostResult, CostStatus, LineItem, LineStatus
from core.domain.validation.options import RevisionInfo
from engines.capacity.service import DeterministicCapacityEngine
from engines.cost.calculator import analyze
from engines.cost.context import CostContext
from engines.cost.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.cost.test_pricing_lookup import record

ARCHITECTURE = uuid.UUID(int=3)
CAPACITY_ID = uuid.UUID(int=4)
AT = datetime(2026, 9, 26, tzinfo=UTC)
REVISION = RevisionInfo(str(ARCHITECTURE), 1, "c" * 64)
EU = {"region": "eu-west-1"}


def link(link_id: str, source: str, target: str, kind: ConnectionKind, **values: Any) -> Connection:
    return connection(
        link_id, source, target, kind=kind, protocol="https", configuration=Configuration(values)
    )


IR = ArchitectureIR(
    "Shop",
    nodes=(
        node("web", NodeKind.CLIENT),
        node("cdn", NodeKind.CDN, configuration=Configuration(
            EU | {"pricing_service": "cloudfront", "pricing_sku": "transfer-out"})),
        node("api", configuration=Configuration(
            EU | {"pricing_service": "ec2", "pricing_sku": "m7g.large", "replicas": 1,
                  "throughput_per_replica_per_second": Decimal(50)})),
        node("files", NodeKind.STORAGE, configuration=Configuration(
            EU | {"pricing_service": "s3", "pricing_sku": "standard", "retention_seconds": 86_400,
                  "storage_bytes": 10**12})),
        node("bus", NodeKind.QUEUE, configuration=Configuration(
            EU | {"deployment_model": "serverless", "pricing_service": "sqs", "pricing_sku": "requests"})),
        node("logs", NodeKind.OBSERVABILITY, configuration=Configuration(
            EU | {"deployment_model": "managed_service", "pricing_service": "logs", "pricing_sku": "gb"})),
    ),
    connections=(
        link("web-cdn", "web", "cdn", ConnectionKind.REQUEST, traffic_ratio=Decimal(1)),
        link("cdn-api", "cdn", "api", ConnectionKind.REQUEST, traffic_ratio=Decimal(1)),
        link("api-files", "api", "files", ConnectionKind.DATA_ACCESS, traffic_ratio=Decimal("0.1"),
             access="write"),
        link("api-bus", "api", "bus", ConnectionKind.PUBLISH, traffic_ratio=Decimal("0.5")),
        link("api-logs", "api", "logs", ConnectionKind.REQUEST, traffic_ratio=Decimal(1)),
    ),
)  # fmt: skip
PRICES = (
    record("cf", service="cloudfront", sku="transfer-out", unit=PricingUnit.GB, unit_price=Decimal("0.085")),
    record("ec2", service="ec2", sku="m7g.large", unit_price=Decimal("0.0816")),
    record("s3", service="s3", sku="standard", unit=PricingUnit.GB_MONTH, unit_price=Decimal("0.023")),
    record("sqs", service="sqs", sku="requests", unit=PricingUnit.REQUEST, unit_price=Decimal("0.40"),
           per=Decimal(10**6)),
    record("cw", service="logs", sku="gb", unit=PricingUnit.GB, unit_price=Decimal("0.50")),
)  # fmt: skip


def workload(**changes: Any) -> WorkloadProfile:
    fields: dict[str, Any] = {
        "name": "Peak",
        "type": WorkloadType.REQUEST_RESPONSE,
        "peak_rate": Quantity.of("100", "requests/second"),
        "average_rate": Quantity.of("25", "requests/second"),  # sustained = 1/4 of the design rate
        "request_payload": Quantity.of("2000", "B"),
        "response_payload": Quantity.of("10000", "B"),
        "read_ratio": Decimal("0.8"),
    }
    return WorkloadProfile(**(fields | changes))


def capacity(load: WorkloadProfile | None = None, ir: ArchitectureIR = IR) -> CapacityBasis:
    request = AnalysisRequest(ARCHITECTURE, 1, load or workload())
    output = DeterministicCapacityEngine().analyze(ir, REVISION, request, ())
    analysis = CapacityAnalysis(CAPACITY_ID, uuid.UUID(int=9), ARCHITECTURE, 1, "c" * 64, "pending", None, AT)
    finished = analysis.start(AT).finish(output.result, AT)
    report = AnalysisReport.of(finished, request.inputs(), output.scaling, output.unsupported_scaling)
    return CapacityBasis.of(report, output.result.components)


def cost(
    basis: CapacityBasis | None, *, from_capacity: bool = False, revision: RevisionInfo = REVISION
) -> CostResult:
    snapshot = PricingSnapshot(uuid.UUID(int=1), uuid.UUID(int=2), "Prices", PRICES, AT)
    request = CostAnalysisRequest(
        ARCHITECTURE, 1, snapshot.id, "USD", date(2026, 9, 26),
        capacity_analysis_id=basis.analysis_id if basis else None, replicas_from_capacity=from_capacity,
    )  # fmt: skip
    return analyze(CostContext(IR, revision, request, snapshot, "aws", basis), default_registry())


def lines(result: CostResult) -> dict[str, LineItem]:
    return {line.element_id: line for line in result.line_items}


def evidence(line: LineItem) -> dict[str, str]:
    return {e.label: e.value for e in line.assumptions}


# --- a compatible capacity analysis ------------------------------------------------------------


def test_usage_is_priced_from_the_capacity_analysis() -> None:
    result = cost(capacity())
    found = lines(result)
    # 730 h x 3600 s = 2,628,000 s at a quarter of the design rate.
    assert found["cdn"].quantity == Decimal(657)  # egress 1,000,000 B/s -> GB
    assert found["cdn"].monthly == Money.of("55.845", "USD")
    assert found["bus"].quantity == Decimal(32_850_000)  # 50 events/s
    assert found["bus"].monthly == Money.of("13.14", "USD")
    assert found["logs"].quantity == Decimal("131.4")  # ingress 200,000 B/s -> GB
    assert found["logs"].monthly == Money.of("65.7", "USD")
    assert found["files"].quantity == Decimal("0.0864")  # 4,000 B/s x 1 day retained, a quarter
    assert found["files"].kind is CostKind.USAGE
    assert found["api"].monthly == Money.of("59.568", "USD")  # the declared replica, fixed
    assert result.status is CostStatus.COMPLETED
    assert "capacity_usage" in [lim.code for lim in result.limitations]


def test_usage_keeps_the_capacity_provenance() -> None:
    basis = capacity()
    found = lines(cost(basis))
    cdn, files = evidence(found["cdn"]), evidence(found["files"])
    assert cdn["capacity_analysis"] == str(CAPACITY_ID)
    assert cdn["capacity_result"] == basis.result_fingerprint
    assert cdn["capacity_model"] == "network-bandwidth@1"
    assert cdn["design_egress_bytes_per_second"] == "1000000"
    assert cdn["average_ratio"] == "0.25 (workload.average_rate / workload.peak_rate)"
    assert files["capacity_model"] == "storage-growth@1"
    assert evidence(found["bus"])["design_work_per_second"] == "50"


def test_required_replicas_are_billed_only_when_asked() -> None:
    basis = capacity()
    declared = lines(cost(basis))["api"]
    scaled = lines(cost(basis, from_capacity=True))["api"]
    assert declared.quantity == Decimal(730)
    assert scaled.quantity == Decimal(1460)  # 2 replicas required for 100 requests/s at 50 each
    assert scaled.monthly == Money.of("119.136", "USD")
    assert evidence(scaled)["capacity_model"] == "replica-throughput@1"
    assert evidence(scaled)["declared_replicas"] == "1"


def test_without_a_scaling_option_the_declared_replicas_are_billed_and_said_so() -> None:
    basis = dataclasses.replace(capacity(), scaling=())
    line = lines(cost(basis, from_capacity=True))["api"]
    assert line.quantity == Decimal(730)
    assert evidence(line)["capacity_scaling"] == "none required or supported: declared replicas billed"


def test_asking_for_required_replicas_needs_a_capacity_analysis() -> None:
    with pytest.raises(InvalidCostRequest) as error:
        CostAnalysisRequest(
            ARCHITECTURE, 1, uuid.UUID(int=1), "USD", date(2026, 9, 26), replicas_from_capacity=True
        )
    assert error.value.details == {"field": "replicas_from_capacity", "reason": "needs_capacity_analysis"}


def test_the_capacity_result_is_part_of_the_fingerprint() -> None:
    first, second = cost(capacity()), cost(capacity())
    assert first.to_dict() == second.to_dict()
    assert first.context_fingerprint != cost(None).context_fingerprint


# --- incompatible analyses -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"revision_number": 2}, "revision"),
        ({"revision_content_hash": "d" * 64}, "revision_content"),
        ({"architecture_id": uuid.UUID(int=99)}, "architecture"),
    ],
)
def test_an_analysis_of_another_revision_is_refused(change: dict[str, Any], reason: str) -> None:
    basis = dataclasses.replace(capacity(), **change)
    with pytest.raises(IncompatibleCapacityAnalysis) as error:
        cost(basis)
    assert error.value.details == {"reason": reason}


def test_an_analysis_the_request_does_not_cite_is_refused() -> None:
    snapshot = PricingSnapshot(uuid.UUID(int=1), uuid.UUID(int=2), "Prices", PRICES, AT)
    uncited = CostAnalysisRequest(ARCHITECTURE, 1, snapshot.id, "USD", date(2026, 9, 26))
    with pytest.raises(IncompatibleCapacityAnalysis):
        CostContext(IR, REVISION, uncited, snapshot, "aws", capacity())
    cited = dataclasses.replace(uncited, capacity_analysis_id=CAPACITY_ID)
    with pytest.raises(IncompatibleCapacityAnalysis):  # cited but not supplied
        CostContext(IR, REVISION, cited, snapshot, "aws", None)


def test_a_failed_capacity_analysis_is_refused() -> None:
    analysis = CapacityAnalysis(
        CAPACITY_ID, uuid.UUID(int=9), ARCHITECTURE, 1, "c" * 64, AnalysisStatus.FAILED.value, None, AT
    )
    with pytest.raises(IncompatibleCapacityAnalysis) as error:
        CapacityBasis.of(AnalysisReport(analysis, {"workload": workload().to_dict()}), ())
    assert error.value.details == {"reason": "no_result"}


# --- missing usage never becomes zero ------------------------------------------------------------


def test_without_a_capacity_analysis_fixed_costs_stay_and_usage_is_unknown() -> None:
    result = cost(None)
    found = lines(result)
    assert found["api"].priced
    assert found["bus"].missing == ("capacity.requests_per_month",)
    assert found["cdn"].missing == ("capacity.transfer_gb_per_month",)
    assert found["files"].monthly == Money.of(23, "USD")  # the declared 1 TB
    assert result.status is CostStatus.PARTIAL
    assert "no_capacity_analysis" in [lim.code for lim in result.limitations]
    assert result.totals.monthly.amount < cost(capacity()).totals.monthly.amount


def test_without_an_average_rate_usage_is_unknown_and_fixed_costs_remain() -> None:
    result = cost(capacity(workload(average_rate=None)))
    found = lines(result)
    assert (found["bus"].status, found["bus"].monthly, found["bus"].missing) == (
        LineStatus.UNKNOWN, None, ("workload.average_rate",),
    )  # fmt: skip
    assert found["cdn"].missing == ("workload.average_rate",)
    assert found["api"].priced
    assert result.status is CostStatus.PARTIAL
    assert result.totals.monthly == Money.of("59.568", "USD") + Money.of("23", "USD")  # api + declared files


def test_missing_workload_values_are_named() -> None:
    found = lines(cost(capacity(workload(response_payload=None))))
    assert found["cdn"].missing == ("capacity.workload.response_payload",)


def test_incomplete_demand_is_never_used_as_a_total() -> None:
    basis = dataclasses.replace(capacity(), incomplete=frozenset({"bus"}))
    found = lines(cost(basis))
    assert found["bus"].missing == ("capacity.demand",)
    assert found["cdn"].priced


def test_a_batch_workload_runs_at_its_design_rate() -> None:
    batch = WorkloadProfile(
        "Nightly", WorkloadType.BATCH, batch_size=1000, batch_interval=Quantity.of("1", "s"),
        request_payload=Quantity.of("2000", "B"),
    )  # fmt: skip
    basis = dataclasses.replace(capacity(), workload=batch)
    assert basis.average_ratio == (Decimal(1), "a batch's design rate is its average rate")


def test_a_component_the_capacity_analysis_does_not_cover_is_unknown() -> None:
    basis = capacity()
    basis = dataclasses.replace(basis, components=tuple(c for c in basis.components if c.node_id != "bus"))
    assert lines(cost(basis))["bus"].missing == ("capacity.component",)


def test_the_basis_keeps_what_cost_needs_of_the_analysis() -> None:
    basis = capacity()
    assert (basis.analysis_id, basis.revision_number, basis.status) == (CAPACITY_ID, 1, "completed")
    assert basis.model_version("network-bandwidth") == 1
    option = basis.required_replicas("api")
    assert option is not None
    assert (option.model_id, option.required.value) == ("replica-throughput", Decimal(2))
    assert basis.required_replicas("cdn") is None
