"""The reliability models and steps this version ships, in precedence order."""

from .availability import MODELS
from .dependency import RequestPaths
from .engine import Registry
from .spof import TopologyFindings


def default_registry() -> Registry:
    return Registry(MODELS, (RequestPaths(), TopologyFindings()))
