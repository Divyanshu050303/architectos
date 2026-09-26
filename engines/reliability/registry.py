"""The reliability models and steps this version ships, in precedence order."""

from .availability import MODELS
from .engine import Registry
from .failure_propagation import RequestPaths
from .spof import TopologyFindings


def default_registry() -> Registry:
    return Registry(MODELS, (RequestPaths(), TopologyFindings()))
