"""Monthly, annual and scenario cost projection (Milestone 8, phase 7): periods under stated
conventions, and scenarios re-priced from the capacity engine's run, never extrapolated."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.scenarios import ConfigurationChange, Scenario, ScenarioResult
from core.domain.cost.analyses import CostAnalysisRequest
from core.domain.cost.capacity import CapacityBasis
from core.domain.cost.errors import InvalidCostRequest
from core.domain.cost.money import BillingPeriod, Money
from core.domain.cost.pricing import PricingModel, PricingRecord, PricingSnapshot, PricingUnit, Tier
from core.domain.cost.projection import CostComparison, CostScenario, PeriodAmounts, ScenarioProjection
from core.domain.cost.results import CostResult, LineStatus
from engines.capacity.service import DeterministicCapacityEngine
from engines.cost.calculator import analyze
from engines.cost.context import CostContext
from engines.cost.projection import baseline_assumptions, project
from engines.cost.registry import default_registry
from tests.unit.cost.test_cost_capacity import ARCHITECTURE, AT, IR, PRICES, REVISION, capacity, workload
from tests.unit.cost.test_cost_results import priced, result
from tests.unit.cost.test_pricing_lookup import record

TIERED_CDN = record(
    "cf", service="cloudfront", sku="transfer-out", unit=PricingUnit.GB, model=PricingModel.TIERED,
    unit_price=None, tiers=(Tier(Decimal(1000), Decimal("0.085")), Tier(None, Decimal("0.02"))),
)  # fmt: skip


def baseline(
    *, with_capacity: bool = True, from_capacity: bool = False, prices: tuple[PricingRecord, ...] = PRICES
) -> tuple[CostContext, CostResult]:
    basis = capacity() if with_capacity else None
    snapshot = PricingSnapshot(uuid.UUID(int=1), uuid.UUID(int=2), "Prices", prices, AT)
    request = CostAnalysisRequest(
        ARCHITECTURE, 1, snapshot.id, "USD", date(2026, 9, 26),
        capacity_analysis_id=basis.analysis_id if basis else None, replicas_from_capacity=from_capacity,
    )  # fmt: skip
    context = CostContext(IR, REVISION, request, snapshot, "aws", basis)
    return context, analyze(context, default_registry())


def capacity_run(scenario: Scenario) -> ScenarioResult:
    request = AnalysisRequest(ARCHITECTURE, 1, workload())
    [run] = DeterministicCapacityEngine().analyze(IR, REVISION, request, (scenario,)).scenarios
    return run


def scenario(name: str = "Double", **changes: Any) -> tuple[CostScenario, Scenario]:
    cost_fields = {
        k: changes.pop(k) for k in ("operating_hours_per_month", "replicas_from_capacity") if k in changes
    }
    capacity_scenario = Scenario(name, **changes)
    return CostScenario(capacity_scenario, **cost_fields), capacity_scenario


def run(
    *, with_capacity: bool = True, from_capacity: bool = False, prices: Any = PRICES, **changes: Any
) -> ScenarioProjection:
    context, base = baseline(with_capacity=with_capacity, from_capacity=from_capacity, prices=prices)
    cost_scenario, capacity_scenario = scenario(**changes)
    return project(
        context,
        base,
        cost_scenario,
        default_registry(),
        capacity_run(capacity_scenario) if with_capacity else None,
    )


def lines(projection: ScenarioProjection) -> dict[str, Any]:
    return {d.element_id: d for d in projection.comparison.changed_lines}


# --- periods -------------------------------------------------------------------------------------


def test_monthly_and_annual_projection_under_stated_conventions() -> None:
    periods = PeriodAmounts(Money.of("730", "USD")).to_dict()
    assert {p: periods[p]["amount"] for p in periods} == {
        "hour": "1",
        "day": "24",
        "month": "730",
        "year": "8760",
    }
    context, base = baseline()
    stated = {e.label: e.value for e in baseline_assumptions(context, base)}
    assert {k: stated[k] for k in ("hours_per_month", "days_per_month", "days_per_year")} == {
        "hours_per_month": "730", "days_per_month": "30.416666666667", "days_per_year": "365",
    }  # fmt: skip
    assert (stated["operating_hours_per_month"], stated["uptime"], stated["currency"]) == ("730", "1", "USD")
    assert stated["pricing_snapshot"].startswith(str(uuid.UUID(int=1)))
    assert stated["workload"] == "Peak (request_response): design 100/s, average 25 requests/second"


def test_operating_hours_change_instance_hours_and_usage_but_not_stored_volume() -> None:
    projection = run(operating_hours_per_month=Decimal(365))
    changed = lines(projection)
    assert changed["api"].scenario_monthly == Money.of("29.784", "USD")  # half of 59.568
    assert changed["cdn"].scenario_quantity == Decimal("328.5")  # half of 657 GB
    assert "files" not in changed  # retained volume does not depend on the hours
    assert projection.comparison.changed_assumptions[0].to_dict() == {
        "label": "operating_hours_per_month", "value": "365",
    }  # fmt: skip
    stated = {e.label: e.value for e in projection.assumptions}
    assert stated["uptime"] == "0.5"


# --- workload scenarios -------------------------------------------------------------------------


def test_increased_workload_doubles_linear_usage_and_steps_replicas() -> None:
    projection = run(growth=Decimal(2), from_capacity=True)
    changed = lines(projection)
    assert changed["cdn"].scenario_quantity == 2 * changed["cdn"].baseline_quantity
    assert changed["bus"].difference == Money.of("13.14", "USD")
    api = changed["api"]
    assert (api.baseline_quantity, api.scenario_quantity) == (Decimal(1460), Decimal(2920))  # 2 -> 4 replicas
    assert api.sensitivity is not None
    assert api.sensitivity.value == "stepwise"
    comparison = projection.comparison
    assert comparison.complete
    assert comparison.percentage == Decimal(100)  # every cost here doubles
    assert [e.label for e in comparison.changed_assumptions] == ["workload_multiplier"]


def test_reduced_workload() -> None:
    projection = run(growth=Decimal("0.5"))
    assert projection.comparison.difference == Money.of("-67.3434936", "USD")  # usage halves
    assert "api" not in lines(projection)  # declared replicas do not change with the workload


def test_tiered_prices_are_not_treated_as_linear() -> None:
    prices = (TIERED_CDN, *PRICES[1:])
    cdn = lines(run(growth=Decimal(2), prices=prices))["cdn"]
    assert cdn.baseline_monthly == Money.of("55.845", "USD")  # 657 GB, all in the first tier
    assert cdn.scenario_monthly == Money.of("91.28", "USD")  # 1000 x 0.085 + 314 x 0.02, not 111.69
    assert cdn.sensitivity is not None
    assert cdn.sensitivity.value == "tiered"


def test_resource_count_changes() -> None:
    projection = run(changes=(ConfigurationChange("api", "replicas", Decimal(3)),))
    changed = lines(projection)
    assert (changed["api"].baseline_monthly, changed["api"].scenario_monthly) == (
        Money.of("59.568", "USD"), Money.of("178.704", "USD"),
    )  # fmt: skip
    assert set(changed) == {"api"}
    assert projection.comparison.changed_assumptions[0].to_dict() == {"label": "api.replicas", "value": "3"}


def test_a_configuration_change_needs_no_capacity_analysis() -> None:
    projection = run(with_capacity=False, changes=(ConfigurationChange("api", "replicas", Decimal(2)),))
    assert lines(projection)["api"].difference == Money.of("59.568", "USD")
    comparison = projection.comparison
    assert not comparison.complete  # usage is unknown on both sides
    assert (comparison.percentage, comparison.percentage_undefined) == (None, "incomplete")
    assert {f"{d.element_id}/{d.resource}" for d in comparison.unknown_differences} == {
        "bus/requests", "cdn/data_transfer", "logs/ingestion",
    }  # fmt: skip


# --- comparisons ---------------------------------------------------------------------------------


def test_the_comparison_reports_totals_differences_and_changes() -> None:
    data = run(growth=Decimal(2)).to_dict()
    comparison = data["comparison"]
    assert (comparison["baseline"]["month"]["amount"], comparison["scenario"]["month"]["amount"]) == (
        "194.2549872", "328.9419744",  # 59.568 fixed + usage x2 (files retained x2)
    )  # fmt: skip
    assert comparison["difference"]["month"]["amount"] == "134.6869872"
    assert comparison["difference"]["year"]["amount"] == "1616.2438464"
    assert comparison["percentage"] == "69.335150227741"
    assert comparison["unknown_differences"] == []
    assert data["status"] == "completed"
    assert {line["element_id"] for line in comparison["changed_lines"]} == {"bus", "cdn", "files", "logs"}


def test_a_zero_baseline_has_no_percentage() -> None:
    comparison = CostComparison.between(result(), result(priced("db")), ())
    assert (comparison.percentage, comparison.percentage_undefined) == (None, "zero_baseline")
    assert comparison.difference == Money.of("189.8", "USD")
    assert comparison.to_dict()["difference"]["year"]["amount"] == "2277.6"


def test_an_unknown_scenario_cost_is_never_a_difference() -> None:
    projection = run(growth=Decimal(2), with_capacity=True, prices=PRICES[1:])  # no CDN price at all
    cdn = lines(projection)["cdn"]
    assert (cdn.baseline_status, cdn.scenario_status, cdn.difference) == (
        LineStatus.UNKNOWN,
        LineStatus.UNKNOWN,
        None,
    )
    assert projection.comparison.percentage is None
    assert "cdn/data_transfer" in projection.comparison.to_dict()["unknown_differences"]


def test_projections_are_reproducible() -> None:
    assert run(growth=Decimal(2)).to_dict() == run(growth=Decimal(2)).to_dict()


def test_periods_of_a_scenario() -> None:
    periods = run(growth=Decimal(2)).to_dict()["periods"]
    assert periods["year"]["amount"] == "3947.3036928"  # 328.9419744 x 12
    assert BillingPeriod.DAY.hours == Decimal(24)


# --- what a scenario cannot do -------------------------------------------------------------------


def test_a_workload_change_needs_a_capacity_analysis() -> None:
    context, base = baseline(with_capacity=False)
    with pytest.raises(InvalidCostRequest) as error:
        project(context, base, scenario(growth=Decimal(2))[0], default_registry())
    assert error.value.details == {"field": "scenarios.workload", "reason": "needs_capacity_analysis"}


def test_the_capacity_run_must_be_of_the_same_scenario() -> None:
    context, base = baseline()
    cost_scenario, _ = scenario(growth=Decimal(2))
    other = capacity_run(Scenario("Other", growth=Decimal(3)))
    with pytest.raises(InvalidCostRequest) as error:
        project(context, base, cost_scenario, default_registry(), other)
    assert error.value.details["reason"] == "capacity_scenario_mismatch"
    with pytest.raises(InvalidCostRequest):
        project(context, base, cost_scenario, default_registry(), None)


@pytest.mark.parametrize("hours", [Decimal(0), Decimal(731), Decimal("-1")])
def test_scenario_operating_hours_are_validated(hours: Decimal) -> None:
    with pytest.raises(InvalidCostRequest):
        CostScenario(Scenario("Hours"), operating_hours_per_month=hours)


def test_the_scenario_basis_is_the_capacity_engines_run() -> None:
    basis = capacity()
    run_ = capacity_run(Scenario("Double", growth=Decimal(2)))
    scenario_basis = basis.for_scenario(workload(), run_.result, run_.scaling)
    assert isinstance(scenario_basis, CapacityBasis)
    assert (scenario_basis.analysis_id, scenario_basis.result_fingerprint) == (
        basis.analysis_id,
        run_.result.fingerprint,
    )
