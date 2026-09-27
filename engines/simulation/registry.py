"""The evaluators this version ships, one per analysis, each wrapping its engine through the engine's
port. Each phase of the milestone adds its evaluator here."""

from core.domain.capacity.ports import CapacityEngine
from engines.capacity.service import DeterministicCapacityEngine

from .capacity import CapacityEvaluator
from .engine import Registry


def default_registry(capacity: CapacityEngine | None = None) -> Registry:
    return Registry([CapacityEvaluator(capacity or DeterministicCapacityEngine())])
