"""The capacity analysis a cost analysis cites for its usage: what it established, never recomputed.

A cost analysis may cite one stored capacity analysis (``capacity_analysis_id``). The basis keeps
what cost needs from it: which revision it analysed (architecture, number and content hash), its
workload (for the average-to-design ratio), its model set and result fingerprint (provenance), its
per-component results (demand, bandwidth, storage), the components whose demand is incomplete (a
lower bound, never used as a total), and its scaling options (required replicas).

It is **compatible** only with a cost analysis of the same architecture and revision, and only if it
produced a result: anything else is refused (``IncompatibleCapacityAnalysis``), never combined.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from core.domain.capacity.analyses import AnalysisReport
from core.domain.capacity.results import (
    AnalysisStatus,
    CapacityResult,
    ComponentResult,
    ModelSet,
    Unsupported,
)
from core.domain.capacity.scenarios import ScalingKind, ScalingOption
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.numbers import arithmetic

from .analyses import CostAnalysisRequest
from .errors import IncompatibleCapacityAnalysis

USABLE = frozenset(
    {
        AnalysisStatus.COMPLETED,
        AnalysisStatus.PARTIAL,
        AnalysisStatus.INSUFFICIENT_INPUT,
        AnalysisStatus.UNSUPPORTED,
    }
)
# What the capacity analysis reports when a component's demand is not fully known (a lower bound).
INCOMPLETE_DEMAND = frozenset(
    {"demand_incomplete", "cyclic_traffic", "mixed_work_units", "demand_overflow", "routing_unspecified"}
)
NO_ENTRY = "no_entry"  # the workload reached nothing: no demand is established anywhere


def _gaps(unsupported: Iterable[Unsupported]) -> tuple[frozenset[str], bool]:
    """The components whose demand is a lower bound, and whether the workload reached nothing."""
    found = tuple(unsupported)
    return frozenset(u.element_id for u in found if u.code in INCOMPLETE_DEMAND), any(
        u.code == NO_ENTRY for u in found
    )


@dataclass(frozen=True, slots=True)
class CapacityBasis:
    analysis_id: uuid.UUID
    architecture_id: uuid.UUID
    revision_number: int
    revision_content_hash: str
    status: str
    workload: WorkloadProfile
    model_set: ModelSet
    result_fingerprint: str
    components: tuple[ComponentResult, ...]
    incomplete: frozenset[str] = frozenset()  # nodes whose demand is a lower bound
    no_entry: bool = False
    scaling: tuple[ScalingOption, ...] = ()
    request_inputs: Mapping[str, Any] = field(default_factory=dict)  # to run it again under a scenario

    @classmethod
    def of(cls, report: AnalysisReport, components: Iterable[ComponentResult]) -> CapacityBasis:
        """From a stored analysis and all its components; refused if it has no result."""
        analysis = report.analysis
        if analysis.status not in USABLE or report.model_set is None or report.result_fingerprint is None:
            raise IncompatibleCapacityAnalysis(details={"reason": "no_result"})
        return cls(
            analysis.id,
            analysis.architecture_id,
            analysis.revision_number,
            analysis.revision_content_hash,
            analysis.status,
            WorkloadProfile.from_dict(report.inputs["workload"]),
            report.model_set,
            report.result_fingerprint,
            tuple(sorted(components, key=lambda c: c.node_id)),
            *_gaps(report.unsupported),
            report.scaling,
            report.inputs,
        )

    def for_scenario(
        self, workload: WorkloadProfile, result: CapacityResult, scaling: tuple[ScalingOption, ...]
    ) -> CapacityBasis:
        """The same analysis under a scenario: the capacity engine's run of it (its workload, result
        and scaling options), for the same revision."""
        return replace(
            self,
            status=result.status.value,
            workload=workload,
            model_set=result.model_set,
            result_fingerprint=result.fingerprint,
            components=tuple(sorted(result.components, key=lambda c: c.node_id)),
            incomplete=_gaps(result.unsupported)[0],
            no_entry=_gaps(result.unsupported)[1],
            scaling=scaling,
        )

    def check(self, request: CostAnalysisRequest, revision_content_hash: str) -> None:
        """Refuse a basis that does not describe the revision being costed."""
        for reason, same in (
            ("analysis", self.analysis_id == request.capacity_analysis_id),
            ("architecture", self.architecture_id == request.architecture_id),
            ("revision", self.revision_number == request.revision_number),
            ("revision_content", self.revision_content_hash == revision_content_hash),
            ("no_result", self.status in USABLE),
        ):
            if not same:
                raise IncompatibleCapacityAnalysis(details={"reason": reason})

    def demand_known(self, node_id: str) -> bool:
        return not self.no_entry and node_id not in self.incomplete

    def component(self, node_id: str) -> ComponentResult | None:
        return next((c for c in self.components if c.node_id == node_id), None)

    @property
    def average_ratio(self) -> tuple[Decimal | None, str]:
        """How the sustained rate relates to the rate the analysis designed for (demand is linear
        in it), and why: a batch's design rate is its average; otherwise average ÷ peak, known only
        when the workload states its average rate."""
        load = self.workload
        if load.type is WorkloadType.BATCH or (
            load.batch_size is not None and load.batch_interval is not None
        ):
            return Decimal(1), "a batch's design rate is its average rate"
        if load.average_rate is None:
            return None, "workload.average_rate"
        with arithmetic():
            ratio = load.average_rate.canonical / load.design_rate
        return ratio, "workload.average_rate / workload.peak_rate"

    def required_replicas(self, node_id: str) -> ScalingOption | None:
        """The replicas a capacity model requires of ``node_id``, if one defines horizontal scaling."""
        options = [s for s in self.scaling if s.node_id == node_id and s.kind is ScalingKind.HORIZONTAL]
        return max(options, key=lambda s: s.required.value, default=None)

    def model_version(self, model_id: str) -> int | None:
        return next((version for mid, version in self.model_set.models if mid == model_id), None)
