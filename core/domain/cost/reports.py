"""A cost analysis as stored and read back: everything but its line items, which are read page by
page. What the result established is kept with the analysis: its totals and summary (breakdowns,
unknown items, drivers), unsupported components, limitations, the projection assumptions, and each
scenario's projection and comparison. Derived views are stored as the engine produced them, so a
stored analysis reads the same whatever changes later."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from core.domain.engine_results import Evidence, Limitation, ModelSet, Unsupported

from .aggregation import CostSummary
from .analyses import CostAnalysis
from .projection import ScenarioProjection
from .results import Totals


@dataclass(frozen=True, slots=True)
class CostReport:
    analysis: CostAnalysis  # without its result
    inputs: Mapping[str, Any]  # CostAnalysisRequest.inputs(), with the scenarios
    organization_id: uuid.UUID
    snapshot_id: uuid.UUID
    currency: str
    snapshot_hash: str | None = None
    model_set: ModelSet | None = None
    context_fingerprint: str | None = None
    result_fingerprint: str | None = None
    totals: Totals | None = None
    summary: Mapping[str, Any] | None = None  # CostSummary.to_dict()
    unsupported: tuple[Unsupported, ...] = ()
    limitations: tuple[Limitation, ...] = ()
    assumptions: tuple[Evidence, ...] = ()
    scenarios: tuple[Mapping[str, Any], ...] = ()  # ScenarioProjection.to_dict() each

    @classmethod
    def of(
        cls,
        analysis: CostAnalysis,
        inputs: Mapping[str, Any],
        organization_id: uuid.UUID,
        summary: CostSummary | None = None,
        assumptions: tuple[Evidence, ...] = (),
        projections: tuple[ScenarioProjection, ...] = (),
    ) -> CostReport:
        """The report of an analysis just executed (its result still attached)."""
        result = analysis.result
        bare = replace(analysis, result=None)
        snapshot_id, currency = uuid.UUID(inputs["snapshot_id"]), inputs["currency"]
        if result is None:
            return cls(bare, inputs, organization_id, snapshot_id, currency)
        return cls(
            bare,
            inputs,
            organization_id,
            snapshot_id,
            currency,
            result.snapshot_hash,
            result.model_set,
            result.context_fingerprint,
            result.fingerprint,
            result.totals,
            summary.to_dict() if summary is not None else None,
            result.unsupported,
            result.limitations,
            assumptions,
            tuple(p.to_dict() for p in projections),
        )
