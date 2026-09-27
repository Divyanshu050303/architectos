"""The evaluators this version ships, one per analysis, each wrapping its engine through the engine's
port. Each phase of the milestone adds its evaluator here."""

from core.domain.capacity.ports import CapacityEngine
from core.domain.reliability.ports import ReliabilityEngine
from engines.capacity.service import DeterministicCapacityEngine
from engines.reliability.service import DeterministicReliabilityEngine

from .capacity import CapacityEvaluator
from .engine import Registry
from .failure import ReliabilityEvaluator


def default_registry(
    capacity: CapacityEngine | None = None, reliability: ReliabilityEngine | None = None
) -> Registry:
    return Registry(
        [
            CapacityEvaluator(capacity or DeterministicCapacityEngine()),
            ReliabilityEvaluator(reliability or DeterministicReliabilityEngine()),
        ]
    )
