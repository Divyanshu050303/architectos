"""The reliability models and steps this version ships, in precedence order."""

from .availability import MODELS
from .cascading_failure import PathFindings
from .engine import Registry
from .failure_propagation import RequestPaths
from .slo import Objectives
from .spof import TopologyFindings


def default_registry() -> Registry:
    return Registry(MODELS, (RequestPaths(), PathFindings(), TopologyFindings(), Objectives()))
