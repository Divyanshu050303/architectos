"""The cost engine: the cost model contract, the registry and the calculation framework.

A **cost model** declares what it prices (``CostModelMeta``: stable id and version, the node kinds
it supports, the resources it bills, the configuration and usage inputs it requires, the units its
charges use, its known limitations) and turns one node into **charges**: what to bill (resource,
category, fixed or usage), how much per month (a quantity in a pricing unit) and which price
(service, SKU, region, conditions). A model never looks up or multiplies a price.

The **framework** prices every charge the same way, so pricing rules cannot drift between models:

1. a charge that lacks its quantity or its price identifiers is an unknown line naming what it
   misses (never 0);
2. the price is looked up exactly in the analysis's snapshot (provider from the project, the
   analysis's currency and pricing date; see ``core/domain/cost/lookup.py``): anything but an exact
   match is an unknown line with the lookup's explanation;
3. the record's own pricing model computes the amount (fixed, per unit, tiered), in exact decimals,
   kept to 12 places; an amount beyond what money holds is unknown (``magnitude``);
4. the line records the price's provenance and freshness, the model and the assumptions.

Nodes run in id order; clients and boundaries are not billed. A node no model applies to is
reported (``no_cost_model``); a model that raises or returns malformed charges is reported
(``model_failed``, ``invalid_output``) and logged, and the node's other models still count.
"""

import decimal
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.cost.errors import InvalidCostResult, InvalidMoney
from core.domain.cost.lookup import PriceQuery, lookup
from core.domain.cost.money import Money
from core.domain.cost.pricing import IDENTIFIER, MAX_CONDITIONS, REGION, SKU, PricingUnit
from core.domain.cost.results import CostCategory, CostKind, CostResult, LineItem, LineStatus, PriceRef
from core.domain.engine_results import (
    MAX_ITEMS,
    Evidence,
    Limitation,
    ModelSet,
    Unsupported,
    evidence_problem,
)

from .context import CostContext

log = logging.getLogger("architectos.cost")

# What the framework adds to a priced line's evidence (after the model's own).
PRICE_EVIDENCE = (
    "provider_source",
    "sku_source",
    "region_source",
    "price_effective_from",
    "price_retrieved_at",
    "price_stale",
)
NOT_BILLED = frozenset({NodeKind.CLIENT, NodeKind.BOUNDARY})
MISSING = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
MAX_MESSAGE = 500
ESTIMATE_NOT_INVOICE = Limitation(
    "estimate_not_invoice",
    "These are estimates from the prices in the snapshot and the architecture's declared "
    "configuration, not an invoice or observed spending.",
)
STALE_PRICING = Limitation(
    "stale_pricing",
    "Some prices were retrieved long before the pricing date, or at an unknown time: the lines "
    "that used them say so.",
)
CAPACITY_USAGE = Limitation(
    "capacity_usage",
    "Usage-priced lines use the cited capacity analysis's demand at the workload's average rate, "
    "sustained for every operating hour; actual usage varies.",
)
NO_CAPACITY = Limitation(
    "no_capacity_analysis",
    "No capacity analysis is cited, so usage-priced resources (requests, transfer, ingestion) are unknown.",
)
NO_PROVIDER = Limitation(
    "no_provider", "The project has no cloud provider set, so no price could be looked up."
)


@dataclass(frozen=True, slots=True)
class CostModelMeta:
    id: str
    version: int
    name: str
    description: str
    kinds: frozenset[NodeKind]
    resources: tuple[str, ...]  # the line resources it bills, e.g. ("instance_hours",)
    units: frozenset[PricingUnit]  # the units its charges use
    configuration: tuple[str, ...] = ()  # IR configuration properties it requires
    usage: tuple[str, ...] = ()  # usage inputs it requires (from a capacity analysis)
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "kinds": sorted(k.value for k in self.kinds),
            "resources": list(self.resources),
            "units": sorted(u.value for u in self.units),
            "configuration": list(self.configuration),
            "usage": list(self.usage),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class Charge:
    """What a model asks to bill for one node. ``quantity`` is per month, in ``unit`` (for a fixed
    monthly charge: 1 month; for instance hours: replicas x operating hours). ``missing`` names what
    the model could not establish (the line is then unknown). ``sku_from`` and ``region_from`` trace
    where the SKU and region were read, and name what to declare when they are absent."""

    resource: str
    category: CostCategory
    kind: CostKind
    unit: PricingUnit
    quantity: Decimal | None
    service: str | None
    sku: str | None
    region: str | None
    conditions: frozenset[str] = frozenset()
    missing: tuple[str, ...] = ()
    assumptions: tuple[Evidence, ...] = ()
    # Where the SKU and region were read (or, when absent, where they should be declared).
    sku_from: str = "configuration.pricing_sku"
    region_from: str = "configuration.region"

    def problems(self) -> list[str]:
        found = []
        if not isinstance(self.resource, str) or not IDENTIFIER.fullmatch(self.resource):
            found.append("resource")
        if not isinstance(self.category, CostCategory) or not isinstance(self.kind, CostKind):
            found.append("category")
        if not isinstance(self.unit, PricingUnit):
            found.append("unit")
        if self.quantity is not None and (
            not isinstance(self.quantity, Decimal) or not self.quantity.is_finite() or self.quantity < 0
        ):
            found.append("quantity")
        if self.service is not None and not (
            isinstance(self.service, str) and IDENTIFIER.fullmatch(self.service)
        ):
            found.append("service")
        if self.sku is not None and not (isinstance(self.sku, str) and SKU.fullmatch(self.sku)):
            found.append("sku")
        if self.region is not None and not (isinstance(self.region, str) and REGION.fullmatch(self.region)):
            found.append("region")
        if (
            not isinstance(self.conditions, frozenset)
            or len(self.conditions) > MAX_CONDITIONS
            or not all(isinstance(c, str) and IDENTIFIER.fullmatch(c) for c in self.conditions)
        ):
            found.append("conditions")
        if not isinstance(self.missing, tuple) or not all(
            isinstance(m, str) and MISSING.fullmatch(m) for m in self.missing
        ):
            found.append("missing")
        if not all(isinstance(m, str) and MISSING.fullmatch(m) for m in (self.sku_from, self.region_from)):
            found.append("sources")
        if evidence_problem(self.assumptions) or len(self.assumptions) > MAX_ITEMS - len(PRICE_EVIDENCE):
            found.append("assumptions")
        return found


@dataclass(frozen=True, slots=True)
class NotPriced:
    """A model's statement that it cannot price this component at all (e.g. it runs on premises):
    reported as unsupported, never as a cost of 0."""

    code: str
    message: str

    def problems(self) -> list[str]:
        ok = isinstance(self.code, str) and MISSING.fullmatch(self.code) and isinstance(self.message, str)
        return [] if ok and 0 < len(self.message) <= MAX_MESSAGE else ["not_priced"]


class CostModel(Protocol):
    @property
    def meta(self) -> CostModelMeta: ...

    def charges(self, node: Node, context: CostContext) -> tuple[Charge | NotPriced, ...]: ...


class DuplicateCostModel(ValueError):
    """A programming error: two models registered under one id."""


class Registry:
    """The cost models this engine knows. Built once, in code."""

    def __init__(self, models: Iterable[CostModel] = ()) -> None:
        self._models: dict[str, CostModel] = {}
        for model in models:
            self.register(model)

    def register(self, model: CostModel) -> None:
        if model.meta.id in self._models:
            raise DuplicateCostModel(f"cost model {model.meta.id!r} is already registered")
        self._models[model.meta.id] = model

    def get(self, model_id: str) -> CostModel | None:
        return self._models.get(model_id)

    def models(self, *, kind: NodeKind | None = None) -> tuple[CostModel, ...]:
        return tuple(m for _, m in sorted(self._models.items()) if kind is None or kind in m.meta.kinds)

    def model_set(self) -> ModelSet:
        return ModelSet.of((m.meta.id, m.meta.version) for m in self._models.values())


def _unknown(
    node: Node, meta: CostModelMeta, charge: Charge, missing: Iterable[str], reason: str
) -> LineItem:
    return LineItem(
        node.id,
        charge.resource,
        charge.category,
        charge.kind,
        LineStatus.UNKNOWN,
        quantity=charge.quantity,
        unit=charge.unit,
        model_id=meta.id,
        model_version=meta.version,
        assumptions=charge.assumptions,
        missing=tuple(missing),
        reason=reason,
    )


def price(node: Node, meta: CostModelMeta, charge: Charge, context: CostContext) -> tuple[LineItem, bool]:
    """The line for ``charge``, and whether its price is stale."""
    missing = [*charge.missing] or (["quantity"] if charge.quantity is None else [])
    unmapped = [
        name
        for name, value in (
            ("configuration.pricing_service", charge.service),
            (charge.sku_from, charge.sku),
            (charge.region_from, charge.region),
            ("project.cloud_provider", context.provider),
        )
        if value is None
    ]
    quantity = charge.quantity
    if quantity is None or missing or unmapped:  # a None quantity is always in ``missing``
        if missing and unmapped:
            reason = "Neither the quantity to bill nor the price this resource maps to is known."
        elif missing:
            reason = "The quantity to bill is not known."
        else:
            reason = "The resource is not mapped to a price."
        return _unknown(node, meta, charge, [*missing, *unmapped], reason), False
    request = context.request
    query = PriceQuery(
        context.provider,  # type: ignore[arg-type]
        charge.service,  # type: ignore[arg-type]
        charge.sku,  # type: ignore[arg-type]
        charge.region,  # type: ignore[arg-type]
        request.currency,
        request.pricing_date,
        charge.conditions,
        charge.unit,
    )
    found = lookup(context.snapshot, query)
    if not found.found or found.record is None or found.freshness is None:
        return _unknown(node, meta, charge, found.missing or ("price",), found.message), False
    record, freshness = found.record, found.freshness
    try:
        monthly = Money.calculated(record.charge(quantity), request.currency)
    except InvalidMoney, decimal.DecimalException:  # beyond 10^15, or beyond 34 digits
        return _unknown(node, meta, charge, ("magnitude",), "The amount is beyond what can be stated."), False
    evidence = (
        *charge.assumptions,
        Evidence("provider_source", "project.cloud_provider"),
        Evidence("sku_source", charge.sku_from),
        Evidence("region_source", charge.region_from),
        Evidence("price_effective_from", freshness.effective_from.isoformat()),
        Evidence(
            "price_retrieved_at", freshness.retrieved_at.isoformat() if freshness.retrieved_at else "unknown"
        ),
        Evidence("price_stale", "true" if freshness.stale else "false"),
    )
    line = LineItem(
        node.id,
        charge.resource,
        charge.category,
        charge.kind,
        LineStatus.PRICED,
        quantity=charge.quantity,
        unit=charge.unit,
        unit_price=record.unit_price,
        per=record.per,
        monthly=monthly,
        price=PriceRef.of(context.snapshot.id, record),
        model_id=meta.id,
        model_version=meta.version,
        assumptions=evidence,
    )
    return line, freshness.stale


def _checked(meta: CostModelMeta, charges: object) -> tuple[Charge | NotPriced, ...]:
    """A model's charges, refused if they are not what the contract says (a model bug)."""
    if not isinstance(charges, tuple) or not charges:
        raise InvalidCostResult(details={"fields": ["charges"]})
    for charge in charges:
        if not isinstance(charge, Charge | NotPriced):
            raise InvalidCostResult(details={"fields": ["charge"]})
        if problems := charge.problems():
            raise InvalidCostResult(details={"fields": problems})
        if isinstance(charge, Charge) and (
            charge.resource not in meta.resources or charge.unit not in meta.units
        ):
            raise InvalidCostResult(details={"fields": ["resource"]})
    return charges


def _node(
    node: Node, context: CostContext, models: tuple[CostModel, ...]
) -> tuple[list[LineItem], list[Unsupported], bool]:
    applicable = [m for m in models if node.kind in m.meta.kinds]
    if not applicable:
        return (
            [],
            [Unsupported(node.id, "no_cost_model", f"No cost model prices a {node.kind} component.")],
            False,
        )
    lines: list[LineItem] = []
    problems: list[Unsupported] = []
    stale = False
    for model in applicable:
        meta = model.meta
        try:
            charges = _checked(meta, model.charges(node, context))
        except InvalidCostResult:
            log.error(
                "cost model produced malformed charges", extra={"model_id": meta.id, "node_id": node.id}
            )
            problems.append(
                Unsupported(
                    node.id, "invalid_output", f"The cost model {meta.id} produced malformed charges."
                )
            )
            continue
        except Exception:  # a crashing model must neither take the others down nor go unnoticed
            log.exception("cost model failed", extra={"model_id": meta.id, "node_id": node.id})
            problems.append(Unsupported(node.id, "model_failed", f"The cost model {meta.id} could not run."))
            continue
        for charge in charges:
            if isinstance(charge, NotPriced):
                problems.append(Unsupported(node.id, charge.code, charge.message))
                continue
            line, was_stale = price(node, meta, charge, context)
            lines.append(line)
            stale = stale or was_stale
    resources = [line.resource for line in lines]
    if len(resources) != len(set(resources)):  # two models billing the same resource of a node
        log.error("cost models billed one resource twice", extra={"node_id": node.id})
        return (
            [],
            [*problems, Unsupported(node.id, "invalid_output", "Two cost models billed the same resource.")],
            False,
        )
    return lines, problems, stale


def analyze(context: CostContext, registry: Registry) -> CostResult:
    """Every applicable model against every billed node; deterministic for equal inputs."""
    models = registry.models()
    lines: list[LineItem] = []
    unsupported: list[Unsupported] = []
    stale = False
    for node in context.ir.nodes:  # id order
        if node.kind in NOT_BILLED:
            continue
        node_lines, problems, node_stale = _node(node, context, models)
        lines += node_lines
        unsupported += problems
        stale = stale or node_stale
    limitations = [ESTIMATE_NOT_INVOICE]
    if stale:
        limitations.append(STALE_PRICING)
    if context.provider is None:
        limitations.append(NO_PROVIDER)
    if context.capacity is not None:
        limitations.append(CAPACITY_USAGE)
    elif any(m.startswith("capacity.") for line in lines for m in line.missing):
        limitations.append(NO_CAPACITY)
    return CostResult(
        currency=context.request.currency,
        snapshot_id=context.snapshot.id,
        snapshot_hash=context.snapshot.content_hash,
        model_set=registry.model_set(),
        context_fingerprint=context.fingerprint,
        line_items=tuple(lines),
        unsupported=tuple(unsupported),
        limitations=tuple(limitations),
    )
