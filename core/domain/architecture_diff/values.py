"""The vocabulary of an architecture diff — three layers kept apart:

- **what changed** (deterministic): each element added, removed or modified, field by field, matched by
  its stable id; its ``ChangeClass`` says what a change *concerns*, never what it *does* — a
  ``scaling`` change is not an improvement;
- **what the engines establish** (deterministic): findings introduced, resolved or unchanged, per
  engine, or why an engine could not compare (``ImpactStatus``);
- **what it may mean** (AI interpretation): explanations whose every statement cites what it rests
  on (its ``Basis``: a change, a group, a finding, a requirement, a decision, a passage, the person's
  context) or is explicitly labelled an inference, for a person to check.

Checking helpers are discovery's (the same validators), raising the diff's own error.
"""

from collections.abc import Iterable
from enum import StrEnum

from core.domain.discovery.values import (
    FINGERPRINT,
    KEY,
    code,
    count,
    digest,
    items,
    key,
    text,
    texts,
)

from .errors import InvalidDiffRecord

__all__ = ["FINGERPRINT", "KEY", "check", "code", "count", "digest", "items", "key", "text", "texts"]

SCHEMA_VERSION = 1  # of the stored diff: changes, groups, impacts


class StateKind(StrEnum):
    REVISION = "revision"  # an accepted, immutable architecture revision
    CANDIDATE = "candidate"  # an architecture agent run's candidate, as reviewed (its content hash)


class ChangeClass(StrEnum):
    """What a change concerns. Never an outcome: ``performance`` does not mean faster."""

    STRUCTURAL = "structural"  # an element added or removed, its kind changed
    TOPOLOGY = "topology"  # how elements connect: endpoints, grouping
    CONFIGURATION = "configuration"
    SCALING = "scaling"  # replicas, autoscaling
    RESOURCES = "resources"  # CPU, memory, storage, instance class
    TECHNOLOGY = "technology"  # the technology or its catalog component
    SECURITY = "security"
    RELIABILITY = "reliability"
    PERFORMANCE = "performance"
    COST = "cost"
    OBSERVABILITY = "observability"
    REQUIREMENT = "requirement"  # requirement traceability
    OPERATIONAL = "operational"  # placement, lifecycle, ownership
    METADATA = "metadata"  # names, descriptions, provenance, metadata only
    UNKNOWN = "unknown"  # a setting the IR does not define, or a value becoming unknown


class Sensitivity(StrEnum):
    PUBLIC = "public"
    SECRET = "secret"  # noqa: S105 - a label; the change is reported, its values never are


class ValueType(StrEnum):
    ABSENT = "absent"
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    LIST = "list"
    OBJECT = "object"
    REDACTED = "redacted"


class RequirementRelation(StrEnum):
    DIRECTLY_CHANGED = "directly_changed"  # a trace to the requirement was added or removed
    ELEMENT_CHANGED = "element_changed"  # an element traced to the requirement changed
    POTENTIAL = "potential"  # the engines' verdict on the requirement differs between the states
    NO_RELATIONSHIP = "no_relationship"  # nothing changed is traced to it
    UNDETERMINED = "undetermined"  # it cannot be read in one of the states


class FindingState(StrEnum):
    INTRODUCED = "introduced"
    RESOLVED = "resolved"
    UNCHANGED = "unchanged"


class ImpactStatus(StrEnum):
    EVALUATED = "evaluated"
    NOT_EVALUATED = "not_evaluated"  # its inputs were not given (a workload, a pricing snapshot)
    FAILED = "failed"


class Basis(StrEnum):
    """What a statement of the AI interpretation rests on."""

    CHANGE = "change"  # a deterministic change
    GROUP = "group"  # a deterministic group of changes
    FINDING = "finding"  # an engine finding
    REQUIREMENT = "requirement"
    DECISION = "decision"
    EVIDENCE = "evidence"  # a retrieved, cited passage
    USER_INPUT = "user_input"  # the comparison context a person gave


class ExplanationStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_NEEDED = "not_needed"  # identical states: nothing to explain, no model call


class ExplanationFailure(StrEnum):
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_TIMEOUT = "llm_timeout"
    LLM_MALFORMED_OUTPUT = "llm_malformed_output"
    EXPLANATION_REJECTED = "explanation_rejected"  # well-formed, but citing what it was not given
    BUDGET_EXHAUSTED = "budget_exhausted"


def check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidDiffRecord(details={"fields": found})
