"""What the drift service asks of the drift engine: a result for inputs it has read and authorized —
the exact baseline revision (with the discovery run it was accepted from, if any), the stored
discovery run, the confirmed identity mappings and the other engines' stored analyses — and the
versions of its rules."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.discovery.results import DiscoveryResult
from core.domain.migrations.evidence import AnalysisEvidence

from .analyses import BaselineRef, DriftRequest, DriftResult, ObservedRef


@dataclass(frozen=True)
class DriftInputs:
    request: DriftRequest
    baseline: BaselineRef
    baseline_ir: ArchitectureIR
    baseline_created_at: datetime
    latest_revision: int
    baseline_source: DiscoveryResult | None  # the run the baseline was accepted from, if it was
    observed: ObservedRef
    observed_result: DiscoveryResult
    observed_at: datetime
    mappings: Mapping[str, str]
    evidence: tuple[AnalysisEvidence, ...] = ()


class DriftEngine(Protocol):
    def run(self, inputs: DriftInputs) -> DriftResult:
        """The comparison of exactly these inputs. Never changes either side."""
        ...

    def versions(self) -> Mapping[str, int]: ...
