import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from .analyses import AnalysisReport, CapacityAnalysis
from .queries import AnalysisQuery, BottleneckQuery, ComponentQuery
from .results import Bottleneck, ComponentResult, Unsupported
from .scenarios import ScalingOption, ScenarioOutcome


class CapacityAnalysisRepository(Protocol):
    """Analyses, their components and bottlenecks are append-only: stored once, finished, never
    changed. Always reached through their project and architecture."""

    async def add(
        self,
        analysis: CapacityAnalysis,
        inputs: Mapping[str, Any],
        scaling: tuple[ScalingOption, ...],
        unsupported_scaling: tuple[Unsupported, ...],
        scenarios: tuple[ScenarioOutcome, ...],
    ) -> AnalysisReport: ...

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> AnalysisReport | None: ...

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: AnalysisQuery
    ) -> list[AnalysisReport]:
        """Newest first, at most ``query.limit``."""
        ...

    async def list_components(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ComponentQuery
    ) -> list[ComponentResult]:
        """By node id after ``query.after``, at most ``query.limit``."""
        ...

    async def list_bottlenecks(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: BottleneckQuery
    ) -> list[Bottleneck]:
        """In the analysis's canonical order (modeled first, worst first)."""
        ...
