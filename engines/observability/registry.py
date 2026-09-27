"""The observability analyzers this version ships, in order (an analyzer building on others comes
after them). Each phase of the milestone adds its analyzers here."""

from .alerts import Alerts
from .criticality import Criticality
from .engine import Registry
from .health import HealthChecks
from .logs import Logs
from .metrics import Metrics
from .policy import Policy
from .slo_monitoring import SloMonitoring
from .traces import Traces


def default_registry() -> Registry:
    return Registry(
        [Criticality(), Logs(), Metrics(), Traces(), HealthChecks(), Alerts(), Policy(), SloMonitoring()]
    )
