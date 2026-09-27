"""Observability values: the dimensions a component's observability is judged on, and the explicit
states of its coverage — never a percentage or a maturity score.

**Dimensions**: ``logging``, ``metrics``, ``tracing``, ``health_checks``, ``alerting``. (SLO
measurability is judged per objective, by the requirement checks; collection paths are part of what
``modeled`` means for logs, metrics and traces.)

**Coverage states**, one per component and dimension, from what the architecture declares:

- ``modeled``: declared, and — for logs, metrics and traces — carried by a modeled path to an
  observability component (a collector or backend);
- ``partial``: declared, but incomplete in the model (no collection path; alert rules without a
  delivery path);
- ``absent``: declared off (``logs: false``, ``metrics: []``, ``traces: false``,
  ``health_check: false``, ``alerts: []``);
- ``unknown``: not declared — never read as present, never as absent;
- ``unsupported``: the dimension does not apply to the component (a third party's internals, an
  observability component's own alerting on itself is judged by its delivery path).

A modeled state says what the architecture declares: it does not prove that telemetry is emitted,
collected, retained, queryable or acted on in production.
"""

from enum import StrEnum

from core.architecture_ir.configuration import METRIC_KINDS, TELEMETRY_SIGNALS

__all__ = ["METRIC_KINDS", "TELEMETRY_SIGNALS", "CoverageState", "Dimension"]


class Dimension(StrEnum):
    LOGGING = "logging"
    METRICS = "metrics"
    TRACING = "tracing"
    HEALTH_CHECKS = "health_checks"
    ALERTING = "alerting"


class CoverageState(StrEnum):
    MODELED = "modeled"
    PARTIAL = "partial"
    ABSENT = "absent"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


# The telemetry signal (IR ``telemetry`` on connections) that carries each collected dimension.
SIGNAL_OF: dict[Dimension, str] = {
    Dimension.LOGGING: "logs",
    Dimension.METRICS: "metrics",
    Dimension.TRACING: "traces",
}
assert set(SIGNAL_OF.values()) == set(TELEMETRY_SIGNALS)  # noqa: S101 - one vocabulary
