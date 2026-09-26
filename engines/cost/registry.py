"""The cost models this version ships (one per resource family; every billed component kind is
covered by exactly one)."""

from .calculator import Registry
from .compute import ComputeModel
from .database import DatabaseModel
from .external import ExternalModel
from .messaging import MessagingModel
from .networking import NetworkingModel
from .observability import ObservabilityModel
from .storage import StorageModel


def default_registry() -> Registry:
    return Registry(
        [
            ComputeModel(),
            DatabaseModel(),
            StorageModel(),
            MessagingModel(),
            NetworkingModel(),
            ObservabilityModel(),
            ExternalModel(),
        ]
    )
