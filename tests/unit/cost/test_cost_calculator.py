"""The cost model contract, registry and calculation framework (Milestone 8, phase 3), with stand-in
models that bill what a table says: the framework alone looks prices up and multiplies."""

import dataclasses
import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.cost.analyses import CostAnalysisRequest
from core.domain.cost.money import Money
from core.domain.cost.pricing import PricingModel, PricingRecord, PricingSnapshot, PricingUnit, Tier
from core.domain.cost.results import CostCategory, CostKind, CostResult, CostStatus, LineStatus
from core.domain.engine_results import Evidence
from core.domain.validation.options import RevisionInfo
from engines.cost.calculator import (
    Charge,
    CostModelMeta,
    DuplicateCostModel,
    Registry,
    analyze,
)
from engines.cost.context import CostContext
from tests.unit.architecture_ir.builders import node
from tests.unit.cost.test_pricing_lookup import record

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
SNAPSHOT_ID = uuid.UUID(int=1)
BILLED = frozenset({NodeKind.SERVICE, NodeKind.DATABASE, NodeKind.QUEUE})


def charge(**overrides: Any) -> Charge:
    fields: dict[str, Any] = {
        "resource": "instance_hours",
        "category": CostCategory.COMPUTE,
        "kind": CostKind.FIXED,
        "unit": PricingUnit.INSTANCE_HOUR,
        "quantity": Decimal(730),
        "service": "rds",
        "sku": "db.r6g.large",
        "region": "eu-west-1",
        "conditions": frozenset({"on_demand"}),
    }
    return Charge(**(fields | overrides))


class Table:
    """Bills, for each node id, the charges it is given (or raises, when ``broken``)."""

    def __init__(
        self,
        charges: dict[str, tuple[Any, ...]],
        model_id: str = "table",
        *,
        broken: bool = False,
        **meta: Any,
    ) -> None:
        self._charges, self._broken = charges, broken
        fields: dict[str, Any] = {
            "id": model_id,
            "version": 1,
            "name": "Table",
            "description": "Bills what the test says.",
            "kinds": BILLED,
            "resources": ("instance_hours", "requests", "platform_fee", "storage"),
            "units": frozenset(PricingUnit),
        }
        self._meta = CostModelMeta(**(fields | meta))

    @property
    def meta(self) -> CostModelMeta:
        return self._meta

    def charges(self, node: Node, context: CostContext) -> tuple[Charge, ...]:
        if self._broken:
            raise RuntimeError("the model is broken")
        return self._charges.get(node.id, ())


def context(
    *records: PricingRecord,
    nodes: tuple[Node, ...] = (node("api"),),
    provider: str | None = "aws",
    currency: str = "USD",
) -> CostContext:
    snapshot = PricingSnapshot(
        SNAPSHOT_ID, uuid.UUID(int=2), "Prices", records or (record(),), datetime(2026, 9, 26, tzinfo=UTC)
    )
    request = CostAnalysisRequest(uuid.UUID(int=3), 1, SNAPSHOT_ID, currency, date(2026, 9, 26))
    return CostContext(ArchitectureIR("Shop", nodes=nodes), REVISION, request, snapshot, provider)


def run(charges: dict[str, tuple[Any, ...]], *records: PricingRecord, **kwargs: Any) -> CostResult:
    return analyze(context(*records, **kwargs), Registry([Table(charges)]))


# --- contract and registry ---------------------------------------------------------------------


def test_duplicate_model_ids_are_refused() -> None:
    with pytest.raises(DuplicateCostModel):
        Registry([Table({}), Table({})])


def test_the_registry_lists_models_by_kind_and_versions_them() -> None:
    registry = Registry([Table({}, "zeta"), Table({}, "alpha", kinds=frozenset({NodeKind.CACHE}))])
    assert [m.meta.id for m in registry.models()] == ["alpha", "zeta"]
    assert [m.meta.id for m in registry.models(kind=NodeKind.CACHE)] == ["alpha"]
    assert registry.get("zeta") is not None
    assert registry.get("missing") is None
    assert registry.model_set().models == (("alpha", 1), ("zeta", 1))


def test_the_meta_describes_the_model() -> None:
    meta = Table({}, configuration=("replicas",), usage=("requests_per_second",)).meta.to_dict()
    assert meta["kinds"] == ["database", "queue", "service"]
    assert (meta["configuration"], meta["usage"]) == (["replicas"], ["requests_per_second"])


# --- calculation --------------------------------------------------------------------------------


def test_instance_hours_are_priced_exactly() -> None:
    result = run({"api": (charge(),)})
    [line] = result.line_items
    assert line.status is LineStatus.PRICED
    assert line.monthly == Money.of("189.8", "USD")  # 730 h x 0.26
    assert line.price is not None
    assert (line.price.record_id, line.price.snapshot_id) == (
        "rds-large",
        SNAPSHOT_ID,
    )
    assert (line.unit_price, line.per, line.model_id, line.model_version) == (
        Decimal("0.26"),
        Decimal(1),
        "table",
        1,
    )
    assert {e.label: e.value for e in line.assumptions} == {
        "price_effective_from": "2026-09-01",
        "price_retrieved_at": "2026-09-20T00:00:00+00:00",
        "price_stale": "false",
    }
    assert (result.status, result.totals.monthly.amount) == (CostStatus.COMPLETED, Decimal("189.8"))
    assert [lim.code for lim in result.limitations] == ["estimate_not_invoice"]


def test_a_fixed_recurring_charge() -> None:
    fee = record(
        "fee", sku="platform", unit=PricingUnit.MONTH, model=PricingModel.FIXED, unit_price=Decimal(50)
    )
    result = run(
        {"api": (charge(resource="platform_fee", category=CostCategory.OTHER, unit=PricingUnit.MONTH,
                        quantity=Decimal(1), sku="platform"),)},
        fee,
    )  # fmt: skip
    assert result.line_items[0].monthly == Money.of(50, "USD")


def test_usage_is_priced_per_block_and_by_graduated_tiers() -> None:
    per_million = record(
        "req", sku="requests", unit=PricingUnit.REQUEST, unit_price=Decimal("0.20"), per=Decimal(10**6)
    )
    tiered = record(
        "gb", sku="storage", unit=PricingUnit.GB_MONTH, model=PricingModel.TIERED, unit_price=None,
        tiers=(Tier(Decimal(100), Decimal("0.10")), Tier(None, Decimal("0.05"))),
    )  # fmt: skip
    usage: dict[str, Any] = {"category": CostCategory.STORAGE, "kind": CostKind.USAGE}
    requests = charge(
        resource="requests", unit=PricingUnit.REQUEST, quantity=Decimal(2_500_000), sku="requests", **usage
    )
    storage = charge(
        resource="storage", unit=PricingUnit.GB_MONTH, quantity=Decimal(250), sku="storage", **usage
    )
    result = run({"api": (requests, storage)}, per_million, tiered)
    assert [line.monthly.amount if line.monthly else None for line in result.line_items] == [
        Decimal("0.5"),
        Decimal("17.5"),
    ]
    assert result.totals.monthly == Money.of(18, "USD")


def test_decimal_precision_is_exact() -> None:
    dime = record(unit_price=Decimal("0.1"))
    result = run({"api": (charge(quantity=Decimal(3)),)}, dime)
    assert result.line_items[0].monthly == Money.of("0.3", "USD")  # never 0.30000000000000004
    tiny = record(unit_price=Decimal("0.000000000001"))
    assert run({"api": (charge(quantity=Decimal(1)),)}, tiny).line_items[0].monthly == Money.of(
        "0.000000000001", "USD"
    )


def test_amounts_are_kept_exact_and_rounded_only_for_display() -> None:
    price = record(unit_price=Decimal("0.0125"))
    line = run({"api": (charge(quantity=Decimal(1)),)}, price).line_items[0]
    assert line.monthly is not None
    assert line.monthly.amount == Decimal("0.0125")
    assert line.monthly.to_dict()["display"] == "0.01"  # half-even at the cent


def test_the_order_of_lines_and_the_result_are_deterministic() -> None:
    nodes = (node("b"), node("a"))
    charges = {"a": (charge(),), "b": (charge(),)}
    first, second = run(charges, nodes=nodes), run(charges, nodes=nodes)
    assert [line.element_id for line in first.line_items] == ["a", "b"]
    assert first.fingerprint == second.fingerprint
    assert first.to_dict() == second.to_dict()


# --- nothing unknown becomes zero ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "missing"),
    [
        ({"quantity": None}, ("quantity",)),
        ({"quantity": None, "missing": ("capacity.requests_per_second",)}, ("capacity.requests_per_second",)),
        ({"sku": None}, ("pricing_sku",)),
        ({"service": None, "region": None}, ("configuration.region", "pricing_service")),
        ({"sku": "db.r6g.xlarge"}, ("price",)),
        ({"region": "us-east-1"}, ("price.region",)),
        ({"conditions": frozenset({"reserved"})}, ("price.conditions",)),
        ({"unit": PricingUnit.HOUR}, ("price.unit",)),
    ],
)
def test_an_unknown_line_names_what_it_misses_and_is_never_zero(
    changes: dict[str, Any], missing: tuple[str, ...]
) -> None:
    result = run({"api": (charge(**changes), charge(resource="storage"))})
    unknown = result.line_items[0]
    assert (unknown.status, unknown.monthly, unknown.missing) == (LineStatus.UNKNOWN, None, missing)
    assert unknown.reason
    assert result.status is CostStatus.PARTIAL
    assert (result.totals.monthly.amount, result.totals.unknown_items, result.totals.complete) == (
        Decimal("189.8"),
        1,
        False,
    )


def test_a_price_in_another_currency_is_not_converted() -> None:
    result = run({"api": (charge(),)}, currency="EUR")
    [line] = result.line_items
    assert (line.status, line.missing) == (LineStatus.UNKNOWN, ("price.currency",))
    assert "USD" in (line.reason or "")
    assert result.status is CostStatus.INSUFFICIENT_PRICING
    assert result.currency == "EUR"


def test_without_a_provider_nothing_is_looked_up() -> None:
    result = run({"api": (charge(),)}, provider=None)
    assert result.line_items[0].missing == ("project.cloud_provider",)
    assert "no_provider" in [lim.code for lim in result.limitations]


def test_an_amount_beyond_what_money_holds_is_unknown() -> None:
    for quantity in (Decimal(10) ** 16, Decimal(10) ** 30):
        [line] = run({"api": (charge(quantity=quantity),)}).line_items
        assert (line.status, line.missing) == (LineStatus.UNKNOWN, ("magnitude",))


def test_a_stale_price_is_used_and_flagged() -> None:
    old = record(retrieved_at=None)
    result = run({"api": (charge(),)}, old)
    line = result.line_items[0]
    assert line.priced
    assert Evidence("price_stale", "true") in line.assumptions
    assert "stale_pricing" in [lim.code for lim in result.limitations]


# --- what models cannot do ----------------------------------------------------------------------


def test_clients_are_not_billed_and_unmodelled_kinds_are_reported() -> None:
    nodes = (node("web", NodeKind.CLIENT), node("cache", NodeKind.CACHE))
    result = run({}, nodes=nodes)
    assert [(u.element_id, u.code) for u in result.unsupported] == [("cache", "no_cost_model")]
    assert result.status is CostStatus.UNSUPPORTED


@pytest.mark.parametrize(
    "bad",
    [
        (charge(quantity=Decimal(-1)),),
        (charge(quantity=Decimal("NaN")),),
        (charge(resource="gpu_hours"),),  # not a resource the model declares
        (charge(service="RDS"),),
        (charge(region="EU WEST"),),
        (charge(assumptions=tuple(Evidence(f"a{i}", "x") for i in range(198))),),
        (),
        ("not a charge",),
    ],
)
def test_malformed_charges_are_reported_not_priced(
    bad: tuple[Any, ...], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, "architectos.cost"):
        result = run({"api": bad})
    assert result.line_items == ()
    assert [(u.element_id, u.code) for u in result.unsupported] == [("api", "invalid_output")]
    assert "malformed charges" in caplog.text


def test_a_failing_model_is_reported_and_the_others_still_count(caplog: pytest.LogCaptureFixture) -> None:
    other = Table({"boom": (charge(resource="storage"),)}, "other")
    with caplog.at_level(logging.ERROR, "architectos.cost"):
        result = analyze(context(nodes=(node("boom"),)), Registry([Table({}, broken=True), other]))
    assert [(u.element_id, u.code) for u in result.unsupported] == [("boom", "model_failed")]
    assert [line.resource for line in result.line_items] == ["storage"]
    assert "cost model failed" in caplog.text
    assert result.status is CostStatus.PARTIAL


def test_two_models_billing_one_resource_are_refused() -> None:
    registry = Registry([Table({"api": (charge(),)}, "one"), Table({"api": (charge(),)}, "two")])
    result = analyze(context(), registry)
    assert result.line_items == ()
    assert [u.code for u in result.unsupported] == ["invalid_output"]


def test_the_context_fingerprint_covers_the_inputs() -> None:
    base = context()
    assert base.fingerprint == context().fingerprint
    assert base.fingerprint != context(provider="gcp").fingerprint
    assert base.fingerprint != context(currency="EUR").fingerprint
    assert (
        base.fingerprint
        != dataclasses.replace(base, revision=RevisionInfo("arch-1", 2, "c" * 64)).fingerprint
    )
    assert base.fingerprint != context(record(unit_price=Decimal("0.27"))).fingerprint
