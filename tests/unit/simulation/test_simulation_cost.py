"""Cost and resource comparison (Milestone 12, phase 7): baseline and scenario priced by the Cost
Engine with one pricing snapshot, compared by its own comparison, usage from the simulation's own
capacity run, missing prices visible, incomplete totals not compared, estimates not invoices."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from core.architecture_ir.serialization import content_hash
from core.domain.cost.pricing import PricingSnapshot
from core.domain.simulations.entities import PricingInputs, SimulationRequest
from core.domain.simulations.results import SYSTEM, Delta, SimulationResult
from core.domain.simulations.scenarios import ConfigurationChange, Scenario, WorkloadChange
from core.domain.simulations.values import AnalysisKind, RunState
from core.domain.validation.options import RevisionInfo
from engines.simulation.context import SimulationContext
from engines.simulation.engine import analyze
from engines.simulation.registry import default_registry
from tests.unit.cost.test_cost_capacity import AT, IR, PRICES, workload

A = AnalysisKind
D = Decimal
ARCHITECTURE = uuid.UUID(int=3)
SNAPSHOT = PricingSnapshot(uuid.UUID(int=1), uuid.UUID(int=2), "Prices", PRICES, AT)
PRICING = PricingInputs(SNAPSHOT.id, date(2026, 9, 26))


def simulate(scenario: Scenario, currency: str | None = "USD", **fields: Any) -> SimulationResult:
    fields.setdefault("pricing", PRICING)
    fields.setdefault("analyses", (A.COST,))
    request = SimulationRequest(ARCHITECTURE, 1, scenario, **fields)
    revision = RevisionInfo(str(ARCHITECTURE), 1, content_hash(IR))
    context = SimulationContext(IR, revision, request, snapshot=SNAPSHOT, provider="aws", currency=currency)
    return analyze(context, default_registry())


def delta(result: SimulationResult, element: str, metric: str) -> Delta:
    [found] = [d for d in result.deltas if (d.element_id, d.metric) == (element, metric)]
    return found


DOUBLE = Scenario("Double", workload=WorkloadChange(growth=D(2)))
REPLICAS = Scenario("Replicas", changes=(ConfigurationChange("api", "replicas", 2),))


def test_a_workload_increase_is_priced_with_the_same_snapshot() -> None:
    result = simulate(DOUBLE, workload=workload())
    [run] = result.runs
    assert run.state is RunState.COMPLETED
    cdn = delta(result, "cdn", "data_transfer.monthly_cost")
    assert (cdn.unit, cdn.baseline, cdn.scenario, cdn.percentage) == (
        "USD/month",
        D("55.845"),
        D("111.69"),
        D(100),
    )
    total = delta(result, SYSTEM, "monthly_cost")
    assert (total.baseline, total.scenario, total.comparable) == (D("194.2549872"), D("328.9419744"), True)
    assert total.percentage == D("69.335150228")
    assert not [d for d in result.deltas if d.element_id == "api"]  # a fixed line does not change
    trace = {e.label: e.value for e in result.trace}
    assert trace["cost.snapshot"] == f"{SNAPSHOT.id} ({SNAPSHOT.content_hash})"  # one snapshot, both sides
    labels = {e.label for e in result.assumptions}
    assert {"cost.estimate", "hours_per_month", "pricing_snapshot", "currency"} <= labels


def test_a_replica_change_is_priced() -> None:
    result = simulate(REPLICAS, workload=workload())
    api = delta(result, "api", "instances.monthly_cost")
    assert (api.baseline, api.scenario, api.difference) == (D("59.568"), D("119.136"), D("59.568"))
    assert delta(result, SYSTEM, "monthly_cost").difference == D("59.568")


def test_without_a_workload_usage_is_unknown_and_the_total_is_not_compared() -> None:
    result = simulate(REPLICAS)
    [run] = result.runs
    assert run.state is RunState.PARTIAL
    total = delta(result, SYSTEM, "monthly_cost")
    assert (total.note, total.comparable, total.difference, total.percentage) == (
        "incomplete",
        False,
        None,
        None,
    )
    cdn = delta(result, "cdn", "data_transfer.monthly_cost")
    assert (cdn.baseline, cdn.scenario, cdn.note) == (None, None, "unknown_line")  # never zero
    assert delta(result, "api", "instances.monthly_cost").difference == D("59.568")  # the fixed line is known


def test_a_missing_price_stays_visible() -> None:
    unpriced = Scenario("Bigger api", changes=(ConfigurationChange("api", "pricing_sku", "m9.huge"),))
    result = simulate(unpriced, workload=workload())
    api = delta(result, "api", "instances.monthly_cost")
    assert (api.baseline, api.scenario, api.note) == (D("59.568"), None, "unknown_line")
    assert delta(result, SYSTEM, "monthly_cost").note == "incomplete"


def test_cost_needs_pricing_inputs_and_a_currency() -> None:
    [no_pricing] = simulate(REPLICAS, pricing=None).runs
    assert (no_pricing.state, no_pricing.reason) == (RunState.UNSUPPORTED, "no_pricing")
    [no_currency] = simulate(REPLICAS, currency=None).runs
    assert (no_currency.state, no_currency.reason) == (RunState.UNSUPPORTED, "no_currency")


def test_capacity_and_cost_read_one_capacity_run() -> None:
    both = simulate(DOUBLE, workload=workload(), analyses=(A.CAPACITY, A.COST))
    runs = {r.analysis: r for r in both.runs}
    assert runs[A.CAPACITY].state is RunState.COMPLETED
    assert runs[A.COST].state is RunState.COMPLETED
    assert delta(both, SYSTEM, "monthly_cost").scenario == D("328.9419744")  # as with cost alone


def test_a_cost_simulation_is_deterministic() -> None:
    assert simulate(DOUBLE, workload=workload()).to_dict() == simulate(DOUBLE, workload=workload()).to_dict()
