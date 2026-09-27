"""The observability engine: the analyzer contract, the registry and the orchestrator.

An **analyzer** declares what it is (``AnalyzerMeta``: stable id and version, its category, the
finding types it may produce, the inputs it reads, the IR properties it relies on, its rules in
words, the analyzers it builds on, what it cannot evaluate, its limitations) and examines the whole
revision once, producing findings and, for requirement and policy analyzers, checks. Analyzers never
persist anything, never call the API, never read telemetry, never modify the architecture and never
run user code: the registry is built in code, and a request can only choose among its analyzers.

The **orchestrator** is generic. It checks the request against the revision (scope nodes are
components of it; selected analyzers are registered, with the analyzers they build on; named
requirements are among the project's in-force ones), records each component's coverage and declared
facts, then runs the selected analyzers in registered order, each seeing the findings before it. An
analyzer's output is checked against its declaration (its own finding types, id and version,
elements of this revision, check keys used once); a malformed output is ``invalid_output``, an
analyzer that raises is ``analyzer_failed`` — recorded with a safe message, logged with the error's
type only, and every other analyzer's findings still count. Findings about nothing in the scope are
dropped.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from core.architecture_ir.component import NodeKind
from core.domain.engine_results import Limitation, ModelSet, Unsupported
from core.domain.observability.errors import InvalidObservabilityRequest, InvalidObservabilityResult
from core.domain.observability.results import (
    CheckResult,
    ComponentResult,
    FindingCategory,
    FindingType,
    ObservabilityFinding,
    ObservabilityResult,
)

from .context import NOT_COMPONENTS, PROPERTY_OF, ObservabilityContext

log = logging.getLogger("architectos.observability")

ARCHITECTURE = "architecture"  # the element id of what concerns the whole revision
INPUTS = frozenset({"components", "connections", "policy", "requirements", "findings"})
OUTPUTS = frozenset({"findings", "checks"})
CONFIGURATION_ONLY = Limitation(
    "configuration_only",
    "This analysis reads the architecture's declared observability configuration. It does not collect "
    "or query live telemetry, does not prove that instrumentation is emitted, collected, retained or "
    "acted on, and does not calculate SLO attainment. Findings require engineering review.",
)
NO_DEFAULTS = Limitation(
    "no_defaults",
    "Nothing is assumed from a technology or a name: a capability the architecture does not declare is "
    "unknown, never taken as present or absent.",
)
NO_COMPONENTS = Limitation("no_components", "There is no component in scope to analyze.")


@dataclass(frozen=True, slots=True)
class AnalyzerMeta:
    id: str
    version: int
    name: str
    description: str
    category: FindingCategory
    finding_types: tuple[FindingType, ...]
    inputs: tuple[str, ...]  # among INPUTS
    properties: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    produces: tuple[str, ...] = ("findings",)  # among OUTPUTS
    requires: tuple[str, ...] = ()  # analyzers whose findings it builds on (registered before it)
    unsupported: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "finding_types": [t.value for t in self.finding_types],
            "inputs": list(self.inputs),
            "properties": list(self.properties),
            "rules": list(self.rules),
            "produces": list(self.produces),
            "requires": list(self.requires),
            "unsupported": list(self.unsupported),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class Progress:
    """What the analysis has established so far, as the next analyzer sees it."""

    findings: tuple[ObservabilityFinding, ...] = ()
    checks: tuple[CheckResult, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalyzerOutput:
    findings: tuple[ObservabilityFinding, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()


class Analyzer(Protocol):
    @property
    def meta(self) -> AnalyzerMeta: ...

    def analyze(self, context: ObservabilityContext, progress: Progress) -> AnalyzerOutput: ...


def names(ids: Iterable[str], shown: int = 20) -> str:
    """Element ids for a sentence: the first ``shown``, then how many more."""
    listed = list(ids)
    head = ", ".join(listed[:shown])
    return head if len(listed) <= shown else f"{head} and {len(listed) - shown} more"


class DuplicateAnalyzer(ValueError):
    """A programming error: two analyzers under one id, or an inconsistent declaration."""


class Registry:
    """The analyzers this engine knows, in their registered order. Built once, in code."""

    def __init__(self, analyzers: Iterable[Analyzer] = ()) -> None:
        self._analyzers: list[Analyzer] = []
        for analyzer in analyzers:
            self.register(analyzer)

    def register(self, analyzer: Analyzer) -> None:
        meta = analyzer.meta
        known = {a.meta.id for a in self._analyzers}
        if meta.id in known:
            raise DuplicateAnalyzer(f"observability analyzer {meta.id!r} is already registered")
        if not meta.produces or not set(meta.produces) <= OUTPUTS or not set(meta.inputs) <= INPUTS:
            raise DuplicateAnalyzer(f"observability analyzer {meta.id!r} declares unknown inputs or outputs")
        if "findings" in meta.produces and not meta.finding_types:
            raise DuplicateAnalyzer(f"observability analyzer {meta.id!r} declares no finding type")
        if not set(meta.requires) <= known:
            raise DuplicateAnalyzer(f"observability analyzer {meta.id!r} builds on one registered after it")
        self._analyzers.append(analyzer)

    def analyzers(self) -> tuple[Analyzer, ...]:
        return tuple(self._analyzers)

    def selected(self, ids: tuple[str, ...] | None) -> tuple[Analyzer, ...]:
        return tuple(a for a in self._analyzers if ids is None or a.meta.id in ids)

    def analyzer_set(self, analyzers: Iterable[Analyzer] | None = None) -> ModelSet:
        chosen = self._analyzers if analyzers is None else analyzers
        return ModelSet.of([(a.meta.id, a.meta.version) for a in chosen])


def _invalid(details: dict[str, str]) -> InvalidObservabilityRequest:
    return InvalidObservabilityRequest(details=details)


def check_request(context: ObservabilityContext, registry: Registry) -> None:
    """The scope names components of this revision; the analyzers are registered, with what they
    build on; named requirements are among the in-force ones. InvalidObservabilityRequest otherwise."""
    for node_id in context.request.scope:
        node = context.topology.node(node_id)
        if node is None or node.kind in NOT_COMPONENTS:
            raise _invalid({"field": "scope", "reason": "unknown_node", "node_id": node_id})
    known = {a.meta.id: a for a in registry.analyzers()}
    selection = context.request.analyzers
    for analyzer_id in selection or ():
        analyzer = known.get(analyzer_id)
        if analyzer is None:
            raise _invalid({"field": "analyzers", "reason": "unknown_analyzer", "analyzer": analyzer_id})
        for required in analyzer.meta.requires:
            if selection is not None and required not in selection:
                raise _invalid(
                    {
                        "field": "analyzers",
                        "reason": "requires_analyzer",
                        "analyzer": analyzer_id,
                        "requires": required,
                    }
                )
    in_force = {r.id for r in context.requirements}
    for requirement_id in context.request.requirement_ids or ():
        if requirement_id not in in_force:
            raise _invalid(
                {
                    "field": "requirement_ids",
                    "reason": "unknown_requirement",
                    "requirement_id": str(requirement_id),
                }
            )


def _checked(
    meta: AnalyzerMeta, output: object, context: ObservabilityContext, used_keys: set[str]
) -> AnalyzerOutput:
    """An analyzer's output, refused if it is not what its declaration says (an analyzer bug)."""
    if not isinstance(output, AnalyzerOutput):
        raise InvalidObservabilityResult(details={"fields": ["output"]})
    nodes = {n.id for n in context.ir.nodes if n.kind is not NodeKind.BOUNDARY}
    connections = {c.id for c in context.ir.connections}
    for finding in output.findings:
        if not isinstance(finding, ObservabilityFinding) or finding.type not in meta.finding_types:
            raise InvalidObservabilityResult(details={"fields": ["findings"]})
        if (finding.analyzer_id, finding.analyzer_version) != (meta.id, meta.version):
            raise InvalidObservabilityResult(details={"fields": ["analyzer_id"]})
        if not (set(finding.node_ids) <= nodes and set(finding.connection_ids) <= connections):
            raise InvalidObservabilityResult(details={"fields": ["elements"]})
    if output.checks and "checks" not in meta.produces:
        raise InvalidObservabilityResult(details={"fields": ["produces"]})
    keys = [c.key for c in output.checks if isinstance(c, CheckResult)]
    if len(keys) != len(output.checks) or len(set(keys)) != len(keys) or used_keys & set(keys):
        raise InvalidObservabilityResult(details={"fields": ["checks"]})
    if not all(isinstance(u, Unsupported) for u in output.unsupported):
        raise InvalidObservabilityResult(details={"fields": ["unsupported"]})
    return output


def _in_scope(finding: ObservabilityFinding, context: ObservabilityContext) -> bool:
    if not context.request.scope:
        return True
    return bool(
        context.component_ids.intersection(finding.node_ids)
        or context.connection_ids.intersection(finding.connection_ids)
    )


def components(context: ObservabilityContext) -> tuple[ComponentResult, ...]:
    """Each component in scope: its declared criticality, coverage per dimension, declared facts and
    what it does not declare (criticality and each dimension's property; nothing for a third party
    but its criticality)."""
    results = []
    for node in context.components:
        facts = context.facts[node.id]
        criticality = facts.known("criticality")
        expected = ["criticality"] + ([] if node.kind is NodeKind.EXTERNAL else list(PROPERTY_OF.values()))
        results.append(
            ComponentResult(
                node.id,
                criticality if isinstance(criticality, str) else None,
                context.coverage(node),
                inputs=facts.evidence(sorted(facts.facts)),
                missing=facts.missing(expected),
            )
        )
    return tuple(results)


def analyze(context: ObservabilityContext, registry: Registry) -> ObservabilityResult:
    """Every selected analyzer against the revision, in registered order; deterministic for equal
    inputs. InvalidObservabilityRequest when the request names what does not exist."""
    check_request(context, registry)
    chosen = registry.selected(context.request.analyzers)
    findings: list[ObservabilityFinding] = []
    checks: list[CheckResult] = []
    unsupported: list[Unsupported] = []
    for analyzer in chosen:
        meta = analyzer.meta
        try:
            output = _checked(
                meta,
                analyzer.analyze(context, Progress(tuple(findings), tuple(checks))),
                context,
                {c.key for c in checks},
            )
        except InvalidObservabilityResult:
            log.error("observability analyzer produced a malformed result", extra={"analyzer_id": meta.id})
            unsupported.append(
                Unsupported(
                    ARCHITECTURE, "invalid_output", f"The analyzer {meta.id} produced a malformed result."
                )
            )
            continue
        except Exception as error:  # one failing analyzer must not take the others down, nor go unnoticed
            log.error(  # no traceback on purpose: its message could carry a configuration value
                "observability analyzer failed",
                extra={"analyzer_id": meta.id, "error_type": type(error).__name__},
            )
            unsupported.append(
                Unsupported(ARCHITECTURE, "analyzer_failed", f"The analyzer {meta.id} could not run.")
            )
            continue
        findings += [f for f in output.findings if _in_scope(f, context)]
        checks += output.checks
        unsupported += output.unsupported
    in_scope = components(context)
    limitations = [CONFIGURATION_ONLY, NO_DEFAULTS] + ([] if in_scope else [NO_COMPONENTS])
    return ObservabilityResult(
        analyzer_set=registry.analyzer_set(chosen),
        context_fingerprint=context.fingerprint,
        components=in_scope,
        findings=tuple(findings),
        checks=tuple(checks),
        unsupported=tuple(unsupported),
        limitations=tuple(limitations),
    )
