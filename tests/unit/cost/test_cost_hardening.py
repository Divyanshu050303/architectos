"""Hardening (Milestone 8, phase 9): determinism under reordering and repetition, the price index
against a plain scan, and numerical edge cases end to end."""

import dataclasses
import random
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.scenarios import Scenario
from core.domain.cost.analyses import CostAnalysisRequest
from core.domain.cost.lookup import PriceIndex, PriceQuery, lookup
from core.domain.cost.money import Money
from core.domain.cost.pricing import PricingModel, PricingRecord, PricingSnapshot, PricingUnit, Tier
from core.domain.cost.projection import CostScenario
from core.domain.cost.results import LineStatus
from engines.cost.calculator import analyze
from engines.cost.context import CostContext
from engines.cost.registry import default_registry
from engines.cost.service import DeterministicCostEngine
from tests.unit.architecture_ir.builders import node
from tests.unit.cost.test_cost_capacity import IR, PRICES, REVISION, capacity
from tests.unit.cost.test_pricing_lookup import record

AT = datetime(2026, 9, 26, tzinfo=UTC)
DAY = date(2026, 9, 26)


def snapshot(records: tuple[PricingRecord, ...]) -> PricingSnapshot:
    return PricingSnapshot(uuid.UUID(int=1), uuid.UUID(int=2), "Prices", records, AT)


def request(**changes: Any) -> CostAnalysisRequest:
    return CostAnalysisRequest(uuid.UUID(int=3), 1, uuid.UUID(int=1), "USD", DAY, **changes)


# --- determinism ---------------------------------------------------------------------------------


def test_the_engine_output_is_identical_on_repetition_and_reordering() -> None:
    basis = capacity()
    scenarios = (
        CostScenario(Scenario("Double", growth=Decimal(2))),
        CostScenario(Scenario("Half", growth=Decimal("0.5"))),
    )
    cost_request = request(capacity_analysis_id=basis.analysis_id, replicas_from_capacity=True)

    def run(ir: ArchitectureIR, records: tuple[PricingRecord, ...]) -> Any:
        output = DeterministicCostEngine().analyze(
            ir, REVISION, cost_request, snapshot(records), "aws", basis, scenarios
        )
        return (output.result.to_dict(), output.summary.to_dict(), [p.to_dict() for p in output.projections])

    first = run(IR, PRICES)
    assert run(IR, PRICES) == first
    shuffled = list(PRICES)
    random.Random(7).shuffle(shuffled)  # noqa: S311 -- a fixed, reproducible order
    reordered_ir = dataclasses.replace(
        IR, nodes=tuple(reversed(IR.nodes)), connections=tuple(reversed(IR.connections))
    )
    assert run(reordered_ir, tuple(shuffled)) == first


@pytest.mark.parametrize(
    "change",
    [
        {"operating_hours_per_month": Decimal(700)},
        {"pricing_date": date(2026, 9, 27)},
        {"assumptions": ()},  # same as the baseline: must not change the fingerprint
    ],
)
def test_the_context_fingerprint_follows_every_input(change: dict[str, Any]) -> None:
    base = CostContext(IR, REVISION, request(), snapshot(PRICES), "aws")
    changed = CostContext(IR, REVISION, dataclasses.replace(base.request, **change), snapshot(PRICES), "aws")
    assert (base.fingerprint == changed.fingerprint) == ("assumptions" in change)


# --- the price index -----------------------------------------------------------------------------


def _scan(records: tuple[PricingRecord, ...], query: PriceQuery) -> tuple[str, str | None]:
    """The lookup's rules applied to every record, without the index (the reference)."""
    same = [
        r for r in records if (r.provider, r.service, r.sku) == (query.provider, query.service, query.sku)
    ]
    here = [r for r in same if r.region == query.region]
    priced = [r for r in here if r.currency == query.currency]
    effective = [r for r in priced if r.effective_on(query.on)]
    matching = [r for r in effective if query.conditions <= set(r.conditions)]
    if not matching:
        return ("none", None)
    latest = max(r.effective_from for r in matching)
    winners = [r for r in matching if r.effective_from == latest]
    if len(winners) > 1:
        return ("ambiguous", None)
    return ("found", winners[0].id) if winners[0].unit is query.unit else ("unit", None)


def test_the_index_finds_what_a_full_scan_finds() -> None:
    rng = random.Random(42)  # noqa: S311 -- reproducible cases, not secrets
    records = tuple(
        record(
            f"r{i}",
            sku=rng.choice(["a", "b", "c"]),
            region=rng.choice(["eu-west-1", "us-east-1"]),
            currency=rng.choice(["USD", "EUR"]),
            effective_from=date(2026, rng.randint(1, 12), 1),
            conditions=tuple(rng.sample(["on_demand", "single_az", "reserved"], rng.randint(0, 2))),
            unit=rng.choice([PricingUnit.INSTANCE_HOUR, PricingUnit.VCPU_HOUR]),
            model=PricingModel.PER_UNIT,
        )
        for i in range(300)
    )
    index = PriceIndex(snapshot(records))
    for _ in range(500):
        query = PriceQuery(
            "aws", "rds", rng.choice(["a", "b", "c", "d"]), rng.choice(["eu-west-1", "us-east-1"]),
            rng.choice(["USD", "EUR"]), date(2026, rng.randint(1, 12), 15),
            frozenset(rng.sample(["on_demand", "single_az"], rng.randint(0, 1))), PricingUnit.INSTANCE_HOUR,
        )  # fmt: skip
        found = lookup(index, query)
        expected = _scan(records, query)
        outcome = {"found": "found", "ambiguous": "ambiguous", "unit_mismatch": "unit"}.get(
            found.outcome.value, "none"
        )
        assert (outcome, found.record.id if found.found and found.record else None) == expected, query


# --- numerics end to end -------------------------------------------------------------------------


def price_one(quantity_values: dict[str, Any], *records: PricingRecord, hours: str = "730") -> Any:
    values = {
        "pricing_service": "rds",
        "pricing_sku": "db.r6g.large",
        "region": "eu-west-1",
    } | quantity_values
    ir = ArchitectureIR("One", nodes=(node("db", NodeKind.CACHE, configuration=Configuration(values)),))
    context = CostContext(
        ir,
        REVISION,
        request(operating_hours_per_month=Decimal(hours)),
        snapshot(records or (record(),)),
        "aws",
    )
    [line] = analyze(context, default_registry()).line_items
    return line


def test_zero_replicas_cost_zero_because_declared_not_because_unknown() -> None:
    line = price_one({"replicas": 0})
    assert (line.status, line.quantity, line.monthly) == (LineStatus.PRICED, Decimal(0), Money.of(0, "USD"))


def test_small_fractional_quantities_and_prices_stay_exact() -> None:
    line = price_one({"replicas": 1}, record(unit_price=Decimal("0.000000000123")), hours="0.001")
    assert line.monthly == Money.of("0.000000000000", "USD")  # 1.23e-13: below the stored 12 places
    line = price_one({"replicas": 3}, record(unit_price=Decimal("0.0000123")), hours="0.5")
    assert line.monthly == Money.of("0.00001845", "USD")


def test_large_values_are_exact_up_to_the_limit_and_unknown_beyond() -> None:
    big = record(unit_price=Decimal("999999"))
    line = price_one({"replicas": 1_000_000}, big)  # 7.3e8 hours x 999999 = 7.29999e14: representable
    assert line.monthly == Money.of("729999270000000", "USD")
    line = price_one({"replicas": 10_000_000}, big)  # 7.3e15: beyond 10^15
    assert (line.status, line.missing) == (LineStatus.UNKNOWN, ("magnitude",))


def test_a_tier_boundary_is_charged_to_its_own_tier() -> None:
    tiered = record(
        model=PricingModel.TIERED, unit_price=None,
        tiers=(Tier(Decimal(730), Decimal("0.26")), Tier(None, Decimal("0.10"))),
    )  # fmt: skip
    at_boundary = price_one({"replicas": 1}, tiered)
    assert at_boundary.monthly == Money.of("189.8", "USD")  # 730 h, all in the first tier
    above = price_one({"replicas": 2}, tiered)
    assert above.monthly == Money.of("262.8", "USD")  # 730 x 0.26 + 730 x 0.10


def test_a_price_per_block_divides_before_it_multiplies() -> None:
    per_million = record(unit_price=Decimal("0.2"), per=Decimal(10**6))
    line = price_one({"replicas": 1}, per_million, hours="0.000001")  # 1e-6 hours of a per-million price
    assert line.monthly == Money.of("0", "USD")
    assert line.status is LineStatus.PRICED
