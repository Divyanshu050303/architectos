"""Compatibility questions: what must hold for the transition to work — between the source and target
data models, schemas and versions, for the applications and clients that use a changed component,
for protocols and connection authentication, while old and new versions run side by side, and for
rolling back.

Each question traces the changes it comes from. None is answered from the architecture's
declarations: a question is ``verified`` only with machine-checkable evidence (a stored analysis
trace), and none exists at planning time, so each stays ``unknown`` with a note on what would
establish it — never assumed compatible.
"""

from collections.abc import Iterator

from core.architecture_ir.diff import ChangeKind
from core.domain.migrations.changes import Aspect, MigrationChange
from core.domain.migrations.steps import CompatibilityAspect, CompatibilityCheck, MigrationStep, Trace
from core.domain.migrations.values import CompatibilityStatus, TraceKind

from .patternbook import BLUE_GREEN_ID, ROLLING_ID, stateful_replacements
from .patterns import PlanningContext

A = CompatibilityAspect
UNKNOWN = CompatibilityStatus.UNKNOWN
AUTHENTICATION = ("configuration.authentication", "configuration.tls")


def _technology(context: PlanningContext, source: bool, element: str) -> str:
    node = context.node(context.source if source else context.target, element)
    if node is None or node.technology is None:
        return "technology not declared"
    return " ".join(p for p in (node.technology.name, node.technology.version) if p)


class _Questions:
    def __init__(self, context: PlanningContext, steps: tuple[MigrationStep, ...]) -> None:
        self.context = context
        self.changes = {c.ref: c for c in context.plannable()}
        self.patterns = {
            (t.reference.split("@")[0], e)
            for s in steps
            for t in s.traces
            if t.kind is TraceKind.PATTERN
            for e in s.element_ids
        }

    def traces(self, *elements: str) -> tuple[Trace, ...]:
        found = [
            Trace(TraceKind.CHANGE, c.ref, c.label) for c in self.changes.values() if c.element_id in elements
        ]
        return tuple(sorted(found, key=lambda t: t.reference))

    def clients(self, element: str) -> str:
        found = [n.id for n in self.context.incoming(self.context.source, element)]
        return ", ".join(found) if found else "its clients"

    def check(self, key: str, aspect: A, question: str, note: str, *elements: str) -> CompatibilityCheck:
        return CompatibilityCheck(key, aspect, UNKNOWN, question, self.traces(*elements), elements, note)

    def replacement(self, old: str, new: str) -> Iterator[CompatibilityCheck]:
        before, after = _technology(self.context, True, old), _technology(self.context, False, new)
        clients = self.clients(old)
        yield self.check(
            f"data_model:{old}", A.DATA_MODEL,
            f"Can {old}'s data ({before}) be represented in {new} ({after}) without loss?",
            "Data models are not in the architecture: a person or a stored analysis must establish it.",
            old, new,
        )  # fmt: skip
        yield self.check(
            f"schema:{old}", A.SCHEMA,
            f"Do the schemas of {old}'s data carry over to {new}?",
            "Schemas are not modeled.", old, new,
        )  # fmt: skip
        if before != after:
            yield self.check(
                f"version:{old}", A.VERSION,
                f"Do the drivers and libraries {clients} use support {after}?",
                "Client libraries and their versions are not modeled.", old, new,
            )  # fmt: skip
        yield self.check(
            f"clients:{old}", A.CLIENT_CHANGES,
            f"Do {clients} need changes (queries, drivers, configuration) to use {new}?",
            "How the clients use the data is not modeled.", old, new,
        )  # fmt: skip
        yield self.check(
            f"authentication:{old}", A.AUTHENTICATION,
            f"Are {new}'s address, credentials and connection settings provided to {clients}?",
            "Credentials are never part of the architecture; a person confirms they are in place.",
            old, new,
        )  # fmt: skip
        yield self.check(
            f"rollback:{old}", A.ROLLBACK,
            f"Can data written to {new} after the cutover be moved back to {old} ({before})?",
            "The reverse data path is not modeled.", old, new,
        )  # fmt: skip

    def technology(self, eid: str) -> Iterator[CompatibilityCheck]:
        before, after = _technology(self.context, True, eid), _technology(self.context, False, eid)
        yield self.check(
            f"application:{eid}", A.APPLICATION,
            f"Does {eid} behave the same on {after} as on {before}?",
            "Application behaviour is not modeled.", eid,
        )  # fmt: skip
        yield self.check(
            f"version:{eid}", A.VERSION,
            f"Do {self.clients(eid)} work with {eid} on {after}?",
            "Interfaces and their versions are not modeled.", eid,
        )  # fmt: skip

    def connection(self, change: MigrationChange) -> Iterator[CompatibilityCheck]:
        eid = change.element_id
        conn = next(c for c in self.context.target.connections if c.id == eid)
        for field in change.changed("protocol"):
            yield self.check(
                f"protocol:{eid}", A.PROTOCOL,
                f"Do {conn.source_id} and {conn.target_id} both support {field.after} (from {field.before})?",
                "Supported protocols are not modeled per component.", eid,
            )  # fmt: skip
        if any(change.changed(name) for name in AUTHENTICATION):
            yield self.check(
                f"authentication:{eid}", A.AUTHENTICATION,
                f"Do both ends of {eid} have what its target authentication and TLS settings require?",
                "Certificates and credentials are not part of the architecture.", eid,
            )  # fmt: skip

    def strategy(self, eid: str) -> Iterator[CompatibilityCheck]:
        if (ROLLING_ID, eid) in self.patterns:
            yield self.check(
                f"mixed_versions:{eid}", A.APPLICATION,
                f"Can instances of {eid} with the source and target configuration serve side by side?",
                "A rolling roll-out runs both for a while; that they are compatible is not modeled.", eid,
            )  # fmt: skip
        if (BLUE_GREEN_ID, eid) in self.patterns:
            yield self.check(
                f"environments:{eid}", A.APPLICATION,
                f"Can both environments of {eid} share its dependencies during the switch?",
                "Shared state between the environments is not modeled.", eid,
            )  # fmt: skip

    def modified(self, change: MigrationChange, replaced: set[str]) -> Iterator[CompatibilityCheck]:
        eid = change.element_id
        if change.element == "node" and Aspect.TECHNOLOGY in change.aspects and eid not in replaced:
            yield from self.technology(eid)
        if change.element == "connection":
            yield from self.connection(change)
        yield from self.strategy(eid)


def compatibility(
    context: PlanningContext, steps: tuple[MigrationStep, ...]
) -> tuple[CompatibilityCheck, ...]:
    """The compatibility questions this transition raises, each traced to its changes and unknown
    until evidence establishes it."""
    questions = _Questions(context, steps)
    pairs = stateful_replacements(context)
    replaced = {e for pair in pairs for e in pair}
    found = [c for old, new in pairs for c in questions.replacement(old, new)]
    for change in sorted(questions.changes.values(), key=lambda c: c.ref):
        if change.change is ChangeKind.MODIFIED:
            found.extend(questions.modified(change, replaced))
    return tuple(sorted({c.key: c for c in found}.values(), key=lambda c: c.key))
