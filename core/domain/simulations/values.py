"""Simulation vocabulary: what a scenario may change, which engines evaluate it, and the explicit
states of what could be established — never a probability, a duration or a prediction.

- **Analyses** a simulation can run on the baseline and on the scenario: ``capacity`` (the Capacity
  Engine: demand, utilization, bottlenecks), ``reliability`` (the Reliability Engine's dependency
  semantics: what a failure interrupts), ``cost`` (the Cost Engine, with one pricing snapshot).
- **Failures** a scenario may declare: a ``component`` or a ``connection`` unavailable, or every
  component declared in a ``zone`` (``availability_zones``) or a ``region`` unavailable. Nothing is
  selected by name, technology or provider.
- **Impact** of a failure on each entry point, from the architecture's declared semantics:
  ``unaffected`` (nothing it requires is unavailable), ``tolerated`` (declared redundancy or
  automatic failover covers the loss), ``degraded`` (only optional dependencies are lost),
  ``interrupted`` (a required dependency is lost with nothing declared to cover it), ``unknown``
  (whether it is covered is not declared: redundancy, failover or independence).
"""

from enum import StrEnum


class AnalysisKind(StrEnum):
    CAPACITY = "capacity"
    RELIABILITY = "reliability"
    COST = "cost"


class FailureKind(StrEnum):
    COMPONENT = "component"
    CONNECTION = "connection"
    ZONE = "zone"
    REGION = "region"


class Impact(StrEnum):
    UNAFFECTED = "unaffected"
    TOLERATED = "tolerated"
    DEGRADED = "degraded"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class RunState(StrEnum):
    """What one analysis established on the baseline and the scenario."""

    COMPLETED = "completed"  # both sides calculated with everything they need
    PARTIAL = "partial"  # both sides calculated; some inputs are unknown or some parts unsupported
    UNSUPPORTED = "unsupported"  # not run: its inputs are missing or the scenario is outside its models
    FAILED = "failed"  # the engine could not produce a result


class SimulationStatus(StrEnum):
    COMPLETED = "completed"  # every requested analysis completed, nothing unsupported or unknown
    PARTIAL = "partial"  # some analysis ran; something is unknown, unsupported or failed
    UNSUPPORTED = "unsupported"  # no analysis could run
    FAILED = "failed"  # no analysis produced a result, and at least one failed
