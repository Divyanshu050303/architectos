"""The observability analyzers this version ships, in order (an analyzer building on others comes
after them). Each phase of the milestone adds its analyzers here."""

from .criticality import Criticality
from .engine import Registry
from .logs import Logs


def default_registry() -> Registry:
    return Registry([Criticality(), Logs()])
