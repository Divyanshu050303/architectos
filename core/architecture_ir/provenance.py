"""Where a value in the architecture came from, and how much it can be trusted.

ArchitectOS keeps apart what a person stated, what was found in real infrastructure, what a model
proposed and what the system assumed. Provenance can be attached to the whole architecture, to an
element (node, connection, assumption) or to one field of it (``configuration.replicas``); the
nearest one applies.

- ``verified``: confirmed by a person or read from the running system. A model proposal or a
  system default can never be verified itself: once a person confirms it, the value's source
  becomes their edit.
- ``inferred``: derived rather than stated or read (e.g. "3 replicas" guessed from an autoscaler).
- ``confidence``: how sure the producer is that it *read or derived the value correctly*; never
  proof, and never a substitute for verification. Required for model proposals.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .errors import Violation, raise_if
from .values import canonical_decimal, clean_line, decimal_places, number_problems, text_problems

MAX_REFERENCE_LENGTH = 500
MAX_ACTOR_LENGTH = 128
CONFIDENCE_PLACES = 3


class ProvenanceSource(StrEnum):
    USER_INPUT = "user_input"  # stated by a person, e.g. in a requirement or a form
    USER_EDIT = "user_edit"  # changed by a person in the architecture
    LLM_PROPOSAL = "llm_proposal"  # proposed by a language model
    TERRAFORM = "terraform"  # read from Terraform configuration or state
    KUBERNETES = "kubernetes"  # read from a Kubernetes cluster or manifests
    CLOUD_DISCOVERY = "cloud_discovery"  # read from a cloud provider's API
    FILE_IMPORT = "file_import"  # read from an architecture file
    SYSTEM_DEFAULT = "system_default"  # filled in by ArchitectOS
    SCHEMA_MIGRATION = "schema_migration"  # produced by upgrading an older IR schema


DISCOVERED_SOURCES = frozenset(
    {
        ProvenanceSource.TERRAFORM,
        ProvenanceSource.KUBERNETES,
        ProvenanceSource.CLOUD_DISCOVERY,
        ProvenanceSource.FILE_IMPORT,
    }
)
UNVERIFIABLE_SOURCES = frozenset({ProvenanceSource.LLM_PROPOSAL, ProvenanceSource.SYSTEM_DEFAULT})


@dataclass(frozen=True, slots=True)
class Provenance:
    source: ProvenanceSource
    reference: str | None = None  # e.g. "aws_db_instance.main", "prod/deployment/api", a file path
    confidence: Decimal | None = None
    verified: bool = False
    inferred: bool = False
    actor: str | None = None  # who or what produced it, e.g. "user:<id>", "discovery:aws"
    recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        if isinstance(self.reference, str):
            object.__setattr__(self, "reference", clean_line(self.reference) or None)
        if isinstance(self.actor, str):
            object.__setattr__(self, "actor", clean_line(self.actor) or None)
        if isinstance(self.confidence, Decimal) and self.confidence.is_finite():
            object.__setattr__(self, "confidence", canonical_decimal(self.confidence))
        raise_if(self.problems())

    @property
    def discovered(self) -> bool:
        return self.source in DISCOVERED_SOURCES

    def problems(self) -> list[Violation]:
        problems: list[Violation] = []
        if not isinstance(self.source, ProvenanceSource):
            problems.append(Violation("invalid_value", "source is not a known provenance source.", "source"))
        problems += text_problems(self.reference, "reference", MAX_REFERENCE_LENGTH, required=False)
        problems += text_problems(self.actor, "actor", MAX_ACTOR_LENGTH, required=False)
        problems += self._confidence_problems()
        for flag in ("verified", "inferred"):
            if not isinstance(getattr(self, flag), bool):
                problems.append(Violation("not_a_boolean", f"{flag} must be true or false.", flag))
        if self.verified is True and self.inferred is True:
            problems.append(
                Violation("contradictory", "A value cannot be both verified and inferred.", "verified")
            )
        if self.verified is True and self.source in UNVERIFIABLE_SOURCES:
            problems.append(
                Violation(
                    "unverifiable",
                    f"A {self.source} cannot be verified; a person confirming it makes it a user_edit.",
                    "verified",
                )
            )
        if self.recorded_at is not None and (
            not isinstance(self.recorded_at, datetime) or self.recorded_at.tzinfo is None
        ):
            problems.append(
                Violation("invalid_timestamp", "recorded_at must be a time with a time zone.", "recorded_at")
            )
        return problems

    def _confidence_problems(self) -> list[Violation]:
        if self.confidence is None:
            if self.source is ProvenanceSource.LLM_PROPOSAL:
                return [Violation("required", "A model proposal must state its confidence.", "confidence")]
            return []
        problems = number_problems(self.confidence, "confidence")
        if problems:
            return problems
        if not Decimal(0) <= self.confidence <= Decimal(1):
            return [Violation("out_of_range", "confidence must be between 0 and 1.", "confidence")]
        if decimal_places(Decimal(self.confidence)) > CONFIDENCE_PLACES:
            return [Violation("too_precise", "confidence has more than 3 decimal places.", "confidence")]
        return []
