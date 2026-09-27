"""Evolution vocabulary: the goals an evolution analysis can evaluate, where its evidence comes from,
what kind of change a candidate proposes, and the explicit states of what could be established —
never a score, a rank or a winner.

- **Goals** are typed and evaluated by an existing engine: a workload to support (capacity), a
  monthly cost ceiling (cost), an availability or recovery objective (reliability), a finding to
  address (the engine that reported it), required observability coverage (observability), or a
  requirement to satisfy (the engine its metric belongs to). Operational complexity, new functional
  requirements and migrations are not goals the engines can evaluate: they are refused, not guessed.
- **Evidence** comes from stored analyses of the architecture, the project's requirements and the
  request's goals. Stored evidence is ``current`` only for the baseline revision's exact content;
  evidence of another revision is ``stale`` and never used; evidence that does not exist is
  ``missing``.
- **Candidates** are configuration changes to existing elements (``scaling``, ``redundancy``,
  ``security_control``, ``instrumentation``): proposals, never applied by the engine. Structural
  changes (a new cache or queue, splitting a service, sharding, more regions) have no model that
  evaluates them: they are reported as considerations for human review, never as candidates.
- **Validation** of a candidate says what the modeled constraints establish: ``valid`` (under the
  modeled constraints only — not production readiness), ``invalid``, ``not_evaluable``,
  ``unsupported``, or ``not_validated`` yet.
"""

from enum import StrEnum


class GoalType(StrEnum):
    INCREASE_WORKLOAD = "increase_workload"  # a rate to support, with its unit
    COST_CEILING = "cost_ceiling"  # a monthly amount not to exceed, in a currency
    AVAILABILITY_OBJECTIVE = "availability_objective"  # a ratio, e.g. 0.999
    RECOVERY_OBJECTIVE = "recovery_objective"  # a recovery time, in seconds
    ADDRESS_FINDING = "address_finding"  # a finding of an engine, by its stable id
    OBSERVABILITY_COVERAGE = "observability_coverage"  # a dimension required where it matters
    SATISFY_REQUIREMENT = "satisfy_requirement"  # an in-force requirement of the project


class GoalSource(StrEnum):
    """Where a goal comes from (its provenance)."""

    USER = "user"  # stated in the request
    REQUIREMENT = "requirement"  # derived from a requirement's structured data


class EvidenceSource(StrEnum):
    """The engine (or input) a piece of evidence comes from; also a goal's evaluation method."""

    CAPACITY = "capacity"
    COST = "cost"
    RELIABILITY = "reliability"
    SECURITY = "security"
    OBSERVABILITY = "observability"
    VALIDATION = "validation"
    SIMULATION = "simulation"
    REQUIREMENT = "requirement"
    GOAL = "goal"


# The engines whose findings have stable ids a goal can name.
FINDING_SOURCES = frozenset(
    {
        EvidenceSource.RELIABILITY,
        EvidenceSource.SECURITY,
        EvidenceSource.OBSERVABILITY,
        EvidenceSource.VALIDATION,
    }
)


class EvidenceState(StrEnum):
    CURRENT = "current"  # of the baseline revision's exact content
    STALE = "stale"  # of another revision (or content): reported, never used
    MISSING = "missing"  # no such evidence exists


class CandidateCategory(StrEnum):
    SCALING = "scaling"  # replicas or resources of an existing component
    REDUNDANCY = "redundancy"  # redundancy groups, failover, placement
    SECURITY_CONTROL = "security_control"  # tls, authentication, authorization, encryption
    INSTRUMENTATION = "instrumentation"  # logs, metrics, traces, health checks, alerts


class ValidationState(StrEnum):
    NOT_VALIDATED = "not_validated"
    VALID = "valid"  # under the modeled constraints only: not production readiness or certification
    INVALID = "invalid"
    NOT_EVALUABLE = "not_evaluable"  # some check could not be decided
    UNSUPPORTED = "unsupported"  # the transformation is not one the architecture domain defines


class ProposalStatus(StrEnum):
    """A candidate is only ever proposed by the engine. Applying one is a separate, authorized change
    to the architecture (a new revision made by a person), never a state the engine sets."""

    PROPOSED = "proposed"


class Basis(StrEnum):
    """What a stated effect of a candidate rests on (kept apart, never collapsed)."""

    MODELED = "modeled"  # derived by an engine's model from the architecture and the evidence
    RULE = "rule"  # stated by the rule that proposed the candidate (documented, versioned)
    CONSIDERATION = "consideration"  # for human review: no model establishes it


class EvolutionStatus(StrEnum):
    COMPLETED = "completed"  # every goal was evaluated on current evidence
    PARTIAL = "partial"  # some goal evaluated; some evidence stale or missing, or a goal unsupported
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # no goal could be evaluated on current evidence
    FAILED = "failed"  # the analysis could not run


class Direction(StrEnum):
    """Which way a candidate moves one dimension, as far as what establishes it goes — never a score."""

    IMPROVES = "improves"
    WORSENS = "worsens"
    MIXED = "mixed"  # some of it better, some worse
    UNCHANGED = "unchanged"
    UNKNOWN = "unknown"  # the engine could not establish it (missing inputs, unsupported)
    NOT_EVALUATED = "not_evaluated"  # the candidate was invalid or unsupported
    CONSIDERATION = "consideration"  # qualitative: for human review, no model decides it
