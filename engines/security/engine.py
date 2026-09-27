"""The security engine: the analyzer contract, the registry and the orchestrator.

An **analyzer** declares what it is (``AnalyzerMeta``: stable id and version, its category, the
finding types it may produce, the inputs it reads, the IR properties it relies on, its rules in
words, the analyzers it builds on, the conditions it cannot evaluate, its limitations) and examines
the whole revision once, producing findings and, for requirement and policy analyzers, checks.
Analyzers never persist anything, never call the API, never modify the architecture and never run
user code: the registry is built in code, and a request can only choose among registered analyzers.

The **orchestrator** is generic. It checks the request against the revision (scope nodes are
components of it; selected analyzers are registered, with the analyzers they build on), records each
component's modeled facts and coverage and the trust zones, then runs the selected analyzers in
registered order, each seeing the findings before it (the threat model builds on the others). What
cannot be established stays explicit:

- an analyzer's output is checked against its declaration: only its own finding types, about
  elements of this revision, with its id and version, check keys used once. A malformed output is
  ``invalid_output``; an analyzer that raises is ``analyzer_failed``. Either is recorded as
  ``Unsupported`` with a safe message and logged with the error's type only (never its message,
  which could carry a configuration value), and every other analyzer's findings still count;
- findings about nothing in the scope are dropped (with a scope, only what touches it is analyzed).
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from core.architecture_ir.component import NodeKind
from core.domain.engine_results import Limitation, ModelSet, Unsupported
from core.domain.security.errors import InvalidSecurityRequest, InvalidSecurityResult
from core.domain.security.inputs import expected
from core.domain.security.results import (
    CheckResult,
    ComponentResult,
    FindingCategory,
    FindingType,
    SecurityFinding,
    SecurityResult,
)

from .context import NOT_COMPONENTS, SecurityContext

log = logging.getLogger("architectos.security")

ARCHITECTURE = "architecture"  # the element id of what concerns the whole revision
INPUTS = frozenset({"components", "connections", "boundaries", "policy", "requirements", "findings"})
OUTPUTS = frozenset({"findings", "checks"})
ARCHITECTURE_LEVEL = Limitation(
    "architecture_level",
    "This is an architecture-level analysis of what the architecture models. It does not prove the "
    "absence of vulnerabilities and does not replace secure implementation review, penetration "
    "testing, dependency scanning or operational security controls. Findings require engineering "
    "review.",
)
NO_DEFAULTS = Limitation(
    "no_defaults",
    "No technology, provider or naming convention is taken as evidence: a control the architecture "
    "does not model is not evaluated, never assumed present or absent.",
)
NO_COMPONENTS = Limitation("no_components", "There is no component in scope to analyze.")


@dataclass(frozen=True, slots=True)
class AnalyzerMeta:
    id: str
    version: int
    name: str
    description: str
    category: FindingCategory
    finding_types: tuple[FindingType, ...]  # what it may produce
    inputs: tuple[str, ...]  # among INPUTS
    properties: tuple[str, ...] = ()  # the IR properties it relies on
    rules: tuple[str, ...] = ()  # each rule, in words
    produces: tuple[str, ...] = ("findings",)  # among OUTPUTS
    requires: tuple[str, ...] = ()  # analyzers whose findings it builds on (registered before it)
    unsupported: tuple[str, ...] = ()  # what it cannot evaluate
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

    findings: tuple[SecurityFinding, ...] = ()
    checks: tuple[CheckResult, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalyzerOutput:
    findings: tuple[SecurityFinding, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()


class Analyzer(Protocol):
    @property
    def meta(self) -> AnalyzerMeta: ...

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput: ...


def names(ids: Iterable[str], shown: int = 20) -> str:
    """Element ids for a sentence: the first ``shown``, then how many more (the element lists of a
    finding carry them all)."""
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
            raise DuplicateAnalyzer(f"security analyzer {meta.id!r} is already registered")
        if not meta.produces or not set(meta.produces) <= OUTPUTS or not set(meta.inputs) <= INPUTS:
            raise DuplicateAnalyzer(f"security analyzer {meta.id!r} declares unknown inputs or outputs")
        if "findings" in meta.produces and not meta.finding_types:
            raise DuplicateAnalyzer(f"security analyzer {meta.id!r} declares no finding type")
        if not set(meta.requires) <= known:
            raise DuplicateAnalyzer(
                f"security analyzer {meta.id!r} builds on an analyzer registered after it"
            )
        self._analyzers.append(analyzer)

    def analyzers(self) -> tuple[Analyzer, ...]:
        return tuple(self._analyzers)

    def selected(self, ids: tuple[str, ...] | None) -> tuple[Analyzer, ...]:
        """The analyzers a request selects (None: all), in registered order."""
        return tuple(a for a in self._analyzers if ids is None or a.meta.id in ids)

    def analyzer_set(self, analyzers: Iterable[Analyzer] | None = None) -> ModelSet:
        chosen = self._analyzers if analyzers is None else analyzers
        return ModelSet.of([(a.meta.id, a.meta.version) for a in chosen])


def check_request(context: SecurityContext, registry: Registry) -> None:
    """The request's scope names components of this revision, and its analyzers are registered,
    with every analyzer they build on. InvalidSecurityRequest otherwise (nothing runs)."""
    topology = context.topology
    for node_id in context.request.scope:
        node = topology.node(node_id)
        if node is None or node.kind in NOT_COMPONENTS:
            raise InvalidSecurityRequest(
                details={"field": "scope", "reason": "unknown_node", "node_id": node_id}
            )
    selection = context.request.analyzers
    if selection is None:
        return
    known = {a.meta.id: a for a in registry.analyzers()}
    for analyzer_id in selection:
        analyzer = known.get(analyzer_id)
        if analyzer is None:
            raise InvalidSecurityRequest(
                details={"field": "analyzers", "reason": "unknown_analyzer", "analyzer": analyzer_id}
            )
        for required in analyzer.meta.requires:
            if required not in selection:
                raise InvalidSecurityRequest(
                    details={
                        "field": "analyzers",
                        "reason": "requires_analyzer",
                        "analyzer": analyzer_id,
                        "requires": required,
                    }
                )


def _checked(
    meta: AnalyzerMeta, output: object, context: SecurityContext, used_keys: set[str]
) -> AnalyzerOutput:
    """An analyzer's output, refused if it is not what its declaration says (an analyzer bug)."""
    if not isinstance(output, AnalyzerOutput):
        raise InvalidSecurityResult(details={"fields": ["output"]})
    nodes = {n.id for n in context.ir.nodes if n.kind is not NodeKind.BOUNDARY}
    boundaries = set(context.boundary_facts)
    connections = {c.id for c in context.ir.connections}
    for finding in output.findings:
        if not isinstance(finding, SecurityFinding) or finding.type not in meta.finding_types:
            raise InvalidSecurityResult(details={"fields": ["findings"]})
        if (finding.analyzer_id, finding.analyzer_version) != (meta.id, meta.version):
            raise InvalidSecurityResult(details={"fields": ["analyzer_id"]})
        if not (
            set(finding.node_ids) <= nodes
            and set(finding.connection_ids) <= connections
            and set(finding.boundary_ids) <= boundaries
        ):
            raise InvalidSecurityResult(details={"fields": ["elements"]})  # stable ids of this revision
    if output.checks and "checks" not in meta.produces:
        raise InvalidSecurityResult(details={"fields": ["produces"]})
    keys = [c.key for c in output.checks if isinstance(c, CheckResult)]
    if len(keys) != len(output.checks) or len(set(keys)) != len(keys) or used_keys & set(keys):
        raise InvalidSecurityResult(details={"fields": ["checks"]})
    if not all(isinstance(u, Unsupported) for u in output.unsupported):
        raise InvalidSecurityResult(details={"fields": ["unsupported"]})
    return output


def _in_scope(finding: SecurityFinding, context: SecurityContext) -> bool:
    if not context.request.scope:
        return True
    return bool(
        context.component_ids.intersection(finding.node_ids)
        or context.connection_ids.intersection(finding.connection_ids)
        or context.scope_zone_ids.intersection(finding.boundary_ids)
    )


def components(context: SecurityContext) -> tuple[ComponentResult, ...]:
    """What each component in scope models: its declared facts (with provenance), what it should
    model but does not, its trust zones, its declared exposure and its sensitivity."""
    results = []
    for node in context.components:
        facts = context.facts[node.id]
        exposure = facts.known("exposure")
        results.append(
            ComponentResult(
                node.id,
                inputs=facts.evidence(sorted(facts.facts)),
                missing=facts.missing(expected(node.kind, facts)),
                trust_zone_ids=context.zones_of.get(node.id, ()),
                exposure=exposure if isinstance(exposure, str) else None,
                sensitive=facts.sensitive,
            )
        )
    return tuple(results)


def analyze(context: SecurityContext, registry: Registry) -> SecurityResult:
    """Every selected analyzer against the revision, in registered order; deterministic for equal
    inputs. InvalidSecurityRequest when the request names nodes or analyzers that do not exist."""
    check_request(context, registry)
    chosen = registry.selected(context.request.analyzers)
    findings: list[SecurityFinding] = []
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
        except InvalidSecurityResult:
            log.error("security analyzer produced a malformed result", extra={"analyzer_id": meta.id})
            unsupported.append(
                Unsupported(
                    ARCHITECTURE, "invalid_output", f"The analyzer {meta.id} produced a malformed result."
                )
            )
            continue
        except Exception as error:  # one failing analyzer must not take the others down, nor go unnoticed
            # the error's type only: its message could carry a configuration value
            log.error(  # no traceback on purpose: it would carry the message
                "security analyzer failed", extra={"analyzer_id": meta.id, "error_type": type(error).__name__}
            )
            unsupported.append(
                Unsupported(ARCHITECTURE, "analyzer_failed", f"The analyzer {meta.id} could not run.")
            )
            continue
        findings += [f for f in output.findings if _in_scope(f, context)]
        checks += output.checks
        unsupported += output.unsupported
    in_scope = components(context)
    limitations = [ARCHITECTURE_LEVEL, NO_DEFAULTS] + ([] if in_scope else [NO_COMPONENTS])
    return SecurityResult(
        analyzer_set=registry.analyzer_set(chosen),
        context_fingerprint=context.fingerprint,
        components=in_scope,
        trust_zones=context.trust_zones,
        findings=tuple(findings),
        checks=tuple(checks),
        unsupported=tuple(unsupported),
        limitations=tuple(limitations),
    )
