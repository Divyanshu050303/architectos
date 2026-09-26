"""Architecture resource cost mapping (Milestone 8, phase 4): components map to prices through their
own configuration (explicit mapping, instance class, inherited region), never by guessing."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.cost.analyses import CostAnalysisRequest
from core.domain.cost.money import Money
from core.domain.cost.pricing import PricingModel, PricingSnapshot, PricingUnit
from core.domain.cost.results import CostCategory, CostKind, CostResult, CostStatus, LineItem, LineStatus
from core.domain.validation.options import RevisionInfo
from engines.cost.calculator import analyze
from engines.cost.context import CostContext
from engines.cost.registry import default_registry
from tests.unit.architecture_ir.builders import node
from tests.unit.cost.test_pricing_lookup import record

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
PRICES = (
    record("rds-large"),  # rds / db.r6g.large / eu-west-1, 0.26 per instance hour, on_demand
    record("rds-xlarge", sku="db.r6g.xlarge", unit_price=Decimal("0.52")),
    record("gp3", sku="gp3", unit=PricingUnit.GB_MONTH, unit_price=Decimal("0.115")),
    record("ec2", service="ec2", sku="m7g.large", unit_price=Decimal("0.0816")),
    record("s3", service="s3", sku="standard", unit=PricingUnit.GB_MONTH, unit_price=Decimal("0.023")),
    record("alb", service="elb", sku="alb", unit=PricingUnit.HOUR, model=PricingModel.FIXED,
           unit_price=Decimal("0.0252")),
    record("auth", service="auth0", sku="b2b-essentials", region="global", unit=PricingUnit.MONTH,
           model=PricingModel.FIXED, unit_price=Decimal(150)),
)  # fmt: skip
DB: dict[str, ConfigValue] = {
    "pricing_service": "rds", "pricing_sku": "db.r6g.large", "region": "eu-west-1", "replicas": 2,
    "storage_bytes": 100_000_000_000, "pricing_storage_sku": "gp3", "pricing_conditions": ("on_demand",),
}  # fmt: skip


def component(node_id: str, kind: NodeKind, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def analyse(*nodes: Node, provider: str | None = "aws", hours: str = "730") -> CostResult:
    snapshot = PricingSnapshot(
        uuid.UUID(int=1), uuid.UUID(int=2), "Prices", PRICES, datetime(2026, 9, 26, tzinfo=UTC)
    )
    request = CostAnalysisRequest(
        uuid.UUID(int=3), 1, snapshot.id, "USD", date(2026, 9, 26), operating_hours_per_month=Decimal(hours)
    )
    context = CostContext(ArchitectureIR("Shop", nodes=nodes), REVISION, request, snapshot, provider)
    return analyze(context, default_registry())


def lines(result: CostResult) -> dict[tuple[str, str], LineItem]:
    return {(line.element_id, line.resource): line for line in result.line_items}


def evidence(line: LineItem) -> dict[str, str]:
    return {e.label: e.value for e in line.assumptions}


def test_a_fully_configured_database_is_priced_resource_by_resource() -> None:
    result = analyse(component("db", NodeKind.DATABASE, **DB))
    found = lines(result)
    instances, storage = found["db", "instances"], found["db", "storage"]
    assert instances.monthly == Money.of("379.6", "USD")  # 2 replicas x 730 h x 0.26
    assert (instances.category, instances.kind, instances.quantity) == (
        CostCategory.DATABASE, CostKind.FIXED, Decimal(1460),
    )  # fmt: skip
    assert storage.monthly == Money.of("11.5", "USD")  # 100 GB x 0.115
    assert storage.category is CostCategory.STORAGE
    assert evidence(instances) | {"price_retrieved_at": "-"} == {
        "mapping": "explicit",
        "instances": "2 (configuration.replicas)",
        "operating_hours": "730",
        "provider_source": "project.cloud_provider",
        "sku_source": "configuration.pricing_sku",
        "region_source": "configuration.region",
        "price_effective_from": "2026-09-01",
        "price_retrieved_at": "-",
        "price_stale": "false",
    }
    assert evidence(storage)["sku_source"] == "configuration.pricing_storage_sku"
    assert (result.status, result.totals.monthly) == (CostStatus.COMPLETED, Money.of("391.1", "USD"))
    assert result.model_set.models == tuple(sorted((m.meta.id, 1) for m in default_registry().models()))


def test_the_instance_class_is_matched_exactly_when_there_is_no_explicit_mapping() -> None:
    values = DB | {"instance_class": "db.r6g.xlarge"}
    del values["pricing_sku"]
    line = lines(analyse(component("db", NodeKind.DATABASE, **values)))["db", "instances"]
    assert line.monthly == Money.of("759.2", "USD")
    assert (evidence(line)["mapping"], evidence(line)["sku_source"]) == (
        "instance_class",
        "configuration.instance_class",
    )


def test_an_explicit_mapping_wins_over_the_instance_class() -> None:
    line = lines(analyse(component("db", NodeKind.DATABASE, **DB, instance_class="db.r6g.xlarge")))[
        "db", "instances"
    ]
    assert line.price is not None
    assert (line.price.record_id, evidence(line)["mapping"]) == ("rds-large", "explicit")


def test_the_region_of_an_enclosing_boundary_is_used_and_traced() -> None:
    region = component("eu", NodeKind.BOUNDARY, boundary_type="region", region="eu-west-1")
    values = {k: v for k, v in DB.items() if k != "region"}
    db = node("db", NodeKind.DATABASE, parent_id="eu", configuration=Configuration(values))
    line = lines(analyse(region, db))["db", "instances"]
    assert line.priced
    assert evidence(line)["region_source"] == "boundary.eu.configuration.region"


@pytest.mark.parametrize(
    ("removed", "missing"),
    [
        ("region", ("configuration.region",)),
        ("replicas", ("configuration.replicas",)),
        ("pricing_service", ("configuration.pricing_service",)),
        ("pricing_sku", ("configuration.pricing_sku",)),
    ],
)
def test_missing_configuration_is_named_never_defaulted(removed: str, missing: tuple[str, ...]) -> None:
    values = {k: v for k, v in DB.items() if k != removed}
    line = lines(analyse(component("db", NodeKind.DATABASE, **values)))["db", "instances"]
    assert (line.status, line.monthly, line.missing) == (LineStatus.UNKNOWN, None, missing)
    assert evidence(line).get("mapping") in (None, "unmapped", "explicit")


def test_missing_storage_leaves_the_instances_priced() -> None:
    values = {k: v for k, v in DB.items() if k not in ("storage_bytes", "pricing_storage_sku")}
    result = analyse(component("db", NodeKind.DATABASE, **values))
    storage = lines(result)["db", "storage"]
    assert storage.missing == ("configuration.pricing_storage_sku", "configuration.storage_bytes")
    assert lines(result)["db", "instances"].priced
    assert (result.status, result.totals.unknown_items) == (CostStatus.PARTIAL, 1)


def test_an_unmapped_component_never_borrows_a_price() -> None:
    result = analyse(component("api", NodeKind.SERVICE, replicas=3, region="eu-west-1"))
    [line] = result.line_items
    assert (line.status, line.missing) == (
        LineStatus.UNKNOWN, ("configuration.pricing_service", "configuration.pricing_sku"),
    )  # fmt: skip
    assert result.status is CostStatus.INSUFFICIENT_PRICING


def test_a_mapping_to_a_price_of_another_unit_is_refused() -> None:
    wrong = DB | {"pricing_storage_sku": "db.r6g.large"}  # an instance-hour price for storage
    storage = lines(analyse(component("db", NodeKind.DATABASE, **wrong)))["db", "storage"]
    assert (storage.status, storage.missing) == (LineStatus.UNKNOWN, ("price.unit",))


def test_an_invalid_mapping_is_refused_by_the_architecture() -> None:
    with pytest.raises(InvalidArchitecture):
        ArchitectureIR("Shop", nodes=(component("db", NodeKind.DATABASE, pricing_sku="db r6g large"),))
    with pytest.raises(InvalidArchitecture):  # storage SKUs only where storage is provisioned
        ArchitectureIR("Shop", nodes=(component("api", NodeKind.SERVICE, pricing_storage_sku="gp3"),))


def test_operating_hours_scale_instance_hours() -> None:
    compute = component("api", NodeKind.SERVICE, pricing_service="ec2", pricing_sku="m7g.large",
                        region="eu-west-1", replicas=3)  # fmt: skip
    line = analyse(compute, hours="200").line_items[0]
    assert (line.quantity, line.monthly) == (Decimal(600), Money.of("48.96", "USD"))


def test_each_resource_family_has_its_model() -> None:
    result = analyse(
        component("files", NodeKind.STORAGE, pricing_service="s3", pricing_sku="standard", region="eu-west-1",
                  storage_bytes=2_000_000_000_000),
        component("lb", NodeKind.LOAD_BALANCER, pricing_service="elb", pricing_sku="alb", region="eu-west-1"),
        component("idp", NodeKind.EXTERNAL, pricing_service="auth0", pricing_sku="b2b-essentials",
                  region="global"),
        component("fn", NodeKind.SERVICE, deployment_model="serverless", pricing_service="lambda",
                  pricing_sku="requests", region="eu-west-1"),
        component("cdn", NodeKind.CDN),
        component("logs", NodeKind.OBSERVABILITY, deployment_model="managed_service"),
        component("bus", NodeKind.QUEUE, deployment_model="serverless"),
        component("web", NodeKind.CLIENT),
    )  # fmt: skip
    found = lines(result)
    assert found["files", "storage"].monthly == Money.of(46, "USD")  # 2000 GB x 0.023
    assert found["lb", "hours"].monthly == Money.of("18.396", "USD")  # 730 h x 0.0252
    assert evidence(found["lb", "hours"])["instances"] == "1 (one per component)"
    assert found["idp", "subscription"].monthly == Money.of(150, "USD")
    usage = {key: line.missing for key, line in found.items() if line.kind is CostKind.USAGE}
    assert usage == {
        ("fn", "requests"): ("capacity.requests_per_month",),
        ("cdn", "data_transfer"): (
            "capacity.transfer_gb_per_month",
            "configuration.pricing_service",
            "configuration.pricing_sku",
            "configuration.region",
        ),
        ("logs", "ingestion"): (
            "capacity.ingested_gb_per_month",
            "configuration.pricing_service",
            "configuration.pricing_sku",
            "configuration.region",
        ),
        ("bus", "requests"): (
            "capacity.requests_per_month",
            "configuration.pricing_service",
            "configuration.pricing_sku",
            "configuration.region",
        ),
    }
    assert "web" not in {line.element_id for line in result.line_items}
    assert result.unsupported == ()


def test_on_premises_components_are_reported_unsupported() -> None:
    result = analyse(component("legacy", NodeKind.DATABASE, deployment_model="on_premises", **DB))
    assert result.line_items == ()
    assert [(u.element_id, u.code) for u in result.unsupported] == [("legacy", "on_premises")]


def test_every_billed_component_kind_has_exactly_one_model() -> None:
    registry = default_registry()
    for kind in set(NodeKind) - {NodeKind.CLIENT, NodeKind.BOUNDARY}:
        assert len(registry.models(kind=kind)) == 1, kind


def test_component_costs_aggregate_stably() -> None:
    nodes = (component("db-b", NodeKind.DATABASE, **DB), component("db-a", NodeKind.DATABASE, **DB))
    first, second = analyse(*nodes), analyse(*reversed(nodes))
    assert first.to_dict() == second.to_dict()
    assert [(s.key, s.monthly.amount) for s in first.by_component()] == [
        ("db-a", Decimal("391.1")), ("db-b", Decimal("391.1")),
    ]  # fmt: skip
    assert first.totals.monthly == Money.of("782.2", "USD")
