"""A connection: communication, data flow or dependency between two nodes.

Its identity is ``id`` (stable across revisions); ``source_id`` → ``target_id`` is the direction
(see ``dependency.py``). Whether the endpoints exist is a graph rule, checked by the architecture.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from .configuration import CONNECTION, CONNECTION_PROPERTIES, Configuration
from .dependency import PROTOCOL, ConnectionKind, Interaction
from .errors import ElementType, Violation, raise_if
from .node import annotation_problems, normalize_annotations
from .provenance import Provenance
from .traceability import RequirementRef
from .values import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    clean_block,
    clean_line,
    id_problems,
    text_problems,
)

CONNECTION_FIELDS = frozenset(
    {
        "source_id",
        "target_id",
        "kind",
        "protocol",
        "interaction",
        "bidirectional",
        "critical",
        "name",
        "description",
    }
)


@dataclass(frozen=True, slots=True)
class Connection:
    id: str
    source_id: str
    target_id: str
    kind: ConnectionKind
    protocol: str | None = None  # "https", "grpc", "postgresql", "kafka", …
    interaction: Interaction | None = None
    bidirectional: bool = False
    critical: bool | None = None  # the source cannot do its job without it; None: not stated
    name: str | None = None  # a short label, e.g. "place order"
    description: str | None = None  # what flows, e.g. "order events"
    configuration: Configuration = field(default_factory=Configuration)
    requirement_refs: tuple[RequirementRef, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)
    provenance: Provenance | None = None
    field_provenance: Mapping[str, Provenance] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.name, str):
            object.__setattr__(self, "name", clean_line(self.name) or None)
        if isinstance(self.description, str):
            object.__setattr__(self, "description", clean_block(self.description) or None)
        if isinstance(self.protocol, str):
            object.__setattr__(self, "protocol", self.protocol.strip().lower() or None)
        normalize_annotations(self)
        raise_if(self.problems())

    @property
    def endpoints(self) -> tuple[str, str]:
        return (self.source_id, self.target_id)

    def problems(self) -> list[Violation]:
        problems = id_problems(self.id, "id")
        problems += id_problems(self.source_id, "source_id")
        problems += id_problems(self.target_id, "target_id")
        if self.source_id == self.target_id:
            problems.append(
                Violation(
                    "self_connection",
                    "A connection must join two different nodes.",
                    "target_id",
                )
            )
        problems += self._semantics_problems()
        problems += text_problems(self.name, "name", MAX_NAME_LENGTH, required=False)
        problems += text_problems(
            self.description, "description", MAX_DESCRIPTION_LENGTH, required=False, block=True
        )
        if not isinstance(self.configuration, Configuration):
            problems.append(
                Violation("invalid_value", "configuration must be a configuration.", "configuration")
            )
            return [p.within(ElementType.CONNECTION, self.id) for p in problems]
        problems += [
            p.within(ElementType.CONNECTION, self.id, "configuration")
            for p in self.configuration.problems_for(CONNECTION_PROPERTIES, CONNECTION)
        ]
        problems += annotation_problems(
            self.requirement_refs,
            self.metadata,
            self.provenance,
            self.field_provenance,
            CONNECTION_FIELDS,
            self.configuration,
        )
        return [p.within(ElementType.CONNECTION, self.id) for p in problems]

    def _semantics_problems(self) -> list[Violation]:
        if not isinstance(self.kind, ConnectionKind):
            return [Violation("invalid_kind", f"kind must be one of: {', '.join(ConnectionKind)}.", "kind")]
        problems: list[Violation] = []
        if self.protocol is not None and (
            not isinstance(self.protocol, str) or not PROTOCOL.fullmatch(self.protocol)
        ):
            problems.append(
                Violation(
                    "invalid_protocol",
                    "protocol must be a lower-case identifier, e.g. https or kafka.",
                    "protocol",
                )
            )
        if self.interaction is not None and not isinstance(self.interaction, Interaction):
            problems.append(
                Violation("invalid_value", "interaction must be synchronous or asynchronous.", "interaction")
            )
        if not isinstance(self.bidirectional, bool):
            problems.append(
                Violation("not_a_boolean", "bidirectional must be true or false.", "bidirectional")
            )
        if self.critical is not None and not isinstance(self.critical, bool):
            problems.append(Violation("not_a_boolean", "critical must be true, false or absent.", "critical"))
        if not self.kind.communicates:
            for name in ("protocol", "interaction"):
                if getattr(self, name) is not None:
                    problems.append(
                        Violation(
                            "not_applicable", f"A dependency does not communicate, so it has no {name}.", name
                        )
                    )
        return problems
