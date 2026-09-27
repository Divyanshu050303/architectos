"""The evaluators this version ships, one per analysis, each wrapping its engine through the engine's
port. Each phase of the milestone adds its evaluator here."""

from core.domain.capacity.ports import CapacityEngine
from core.domain.cost.ports import CostEngine
from core.domain.reliability.ports import ReliabilityEngine
from engines.capacity.service import DeterministicCapacityEngine
from engines.cost.service import DeterministicCostEngine
from engines.reliability.service import DeterministicReliabilityEngine

from .capacity import CapacityEvaluator
from .cost import CostEvaluator
from .engine import Registry
from .failure import ReliabilityEvaluator


def default_registry(
    capacity: CapacityEngine | None = None,
    reliability: ReliabilityEngine | None = None,
    cost: CostEngine | None = None,
) -> Registry:
    capacity = capacity or DeterministicCapacityEngine()
    return Registry(
        [
            CapacityEvaluator(capacity),
            ReliabilityEvaluator(reliability or DeterministicReliabilityEngine()),
            CostEvaluator(cost or DeterministicCostEngine(capacity=capacity), capacity),
        ]
    )
