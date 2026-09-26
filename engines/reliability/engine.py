"""The reliability engine: the model contract, the registry and the orchestrator.

A **component model** declares what it is (``ModelMeta``: stable id and version, the node kinds it
supports, what it estimates, the configuration properties it requires, its assumptions, its formula,
the conditions it does not support, its limitations) and turns one node's declared facts into
estimates (``availability``, ``replica_availability``, ``recovery_time``, ``data_loss_window``),
each with its source, basis and inputs, and possibly findings about that node. Models never persist
anything, never call the API and never run user code: the registry is built in code.

An **architecture step** (``StepMeta``) works on the whole revision once the components are known:
request paths and their availability, topology findings, objective verdicts. Steps run in their
registered order, each seeing what the steps before it produced.

The **orchestrator** is generic. For every node in scope (not clients, not boundaries), in id order,
it runs the applicable models in registered order (the order is precedence: a model is not run when
everything it estimates is already known, and later models see earlier estimates, e.g. a replica's
availability before the redundancy that combines replicas), then the steps. What cannot be
calculated is explicit:

- a node no model applies to is ``unsupported`` (``no_reliability_model``);
- a model whose required properties are missing (absent or unknown) is not run; the node is
  ``insufficient_input`` with the missing properties named, unless something was estimated;
- a model or step that raises or returns malformed output is recorded as ``Unsupported``
  (``model_failed``, ``invalid_output``, ``step_failed``) with a safe message, logged, and
  everything else still counts.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.capacity.results import ComponentStatus, Estimate, Source
from core.domain.engine_results import Limitation, ModelSet, Unsupported
from core.domain.reliability.errors import InvalidReliabilityRequest, InvalidReliabilityResult
from core.domain.reliability.inputs import ComponentReliability
from core.domain.reliability.results import (
    ComponentResult,
    ObjectiveResult,
    PathResult,
    ReliabilityFinding,
    ReliabilityResult,
)

from .context import ReliabilityContext

log = logging.getLogger("architectos.reliability")

OUT_OF_SCOPE = frozenset({NodeKind.CLIENT, NodeKind.BOUNDARY})  # request sources and groupings
ARCHITECTURE = "architecture"  # the element id of what concerns the whole revision
RESOURCES = frozenset({"availability", "replica_availability", "recovery_time", "data_loss_window"})
NOT_MEASURED = Limitation(
    "not_measured",
    "Nothing here is measured on a running system: every value is declared in the architecture or "
    "derived from declared values by a stated model. Estimates are not guaranteed uptime.",
)
NO_CATALOG = Limitation(
    "no_catalog",
    "No component catalog or provider commitment is used: what the architecture does not declare "
    "stays unknown, never a typical value.",
)
NO_COMPONENTS = Limitation(
    "no_components", "The architecture has no component a reliability model applies to."
)


@dataclass(frozen=True, slots=True)
class ModelMeta:
    id: str
    version: int
    name: str
    description: str
    kinds: frozenset[NodeKind]
    resources: tuple[str, ...]  # what it estimates, among RESOURCES
    configuration: tuple[str, ...] = ()  # required IR configuration properties
    assumptions: tuple[str, ...] = ()  # what the formula takes as true, in words
    formula: str = ""
    unsupported: tuple[str, ...] = ()  # conditions under which it gives no value
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "kinds": sorted(k.value for k in self.kinds),
            "resources": list(self.resources),
            "configuration": list(self.configuration),
            "assumptions": list(self.assumptions),
            "formula": self.formula,
            "unsupported": list(self.unsupported),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class ModelInputs:
    node: Node
    facts: ComponentReliability
    context: ReliabilityContext
    estimates: tuple[Estimate, ...]  # what earlier models estimated for this node

    def estimate(self, resource: str) -> Estimate | None:
        return next((e for e in self.estimates if e.resource == resource and e.known), None)


@dataclass(frozen=True, slots=True)
class ModelOutput:
    """At least one estimate or finding. An unknown estimate names what it is missing."""

    estimates: tuple[Estimate, ...] = ()
    findings: tuple[ReliabilityFinding, ...] = ()


class ReliabilityModel(Protocol):
    @property
    def meta(self) -> ModelMeta: ...

    def estimate(self, inputs: ModelInputs) -> ModelOutput: ...


@dataclass(frozen=True, slots=True)
class StepMeta:
    id: str
    version: int
    name: str
    description: str
    produces: tuple[str, ...]  # among "paths", "findings", "objectives"
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "produces": list(self.produces),
            "assumptions": list(self.assumptions),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class Progress:
    """What the analysis has established so far, as the steps see it."""

    components: tuple[ComponentResult, ...]
    paths: tuple[PathResult, ...] = ()
    findings: tuple[ReliabilityFinding, ...] = ()

    def component(self, node_id: str) -> ComponentResult | None:
        return next((c for c in self.components if c.node_id == node_id), None)


@dataclass(frozen=True, slots=True)
class StepOutput:
    paths: tuple[PathResult, ...] = ()
    findings: tuple[ReliabilityFinding, ...] = ()
    objectives: tuple[ObjectiveResult, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()


class ArchitectureStep(Protocol):
    @property
    def meta(self) -> StepMeta: ...

    def run(self, context: ReliabilityContext, progress: Progress) -> StepOutput: ...


class DuplicateModel(ValueError):
    """A programming error: two models or steps registered under one id."""


class Registry:
    """The models and steps this engine knows, in their registered order. Built once, in code."""

    def __init__(
        self, models: Iterable[ReliabilityModel] = (), steps: Iterable[ArchitectureStep] = ()
    ) -> None:
        self._models: list[ReliabilityModel] = []
        self._steps: list[ArchitectureStep] = []
        for model in models:
            self.register(model)
        for step in steps:
            self.register_step(step)

    def _ids(self) -> set[str]:
        return {m.meta.id for m in self._models} | {s.meta.id for s in self._steps}

    def register(self, model: ReliabilityModel) -> None:
        if model.meta.id in self._ids():
            raise DuplicateModel(f"reliability model {model.meta.id!r} is already registered")
        if not set(model.meta.resources) <= RESOURCES or not model.meta.resources:
            raise DuplicateModel(f"reliability model {model.meta.id!r} estimates unknown resources")
        self._models.append(model)

    def register_step(self, step: ArchitectureStep) -> None:
        if step.meta.id in self._ids():
            raise DuplicateModel(f"reliability step {step.meta.id!r} is already registered")
        self._steps.append(step)

    def models(self, *, kind: NodeKind | None = None) -> tuple[ReliabilityModel, ...]:
        """In registered (precedence) order; those supporting ``kind`` when given."""
        return tuple(m for m in self._models if kind is None or kind in m.meta.kinds)

    def steps(self) -> tuple[ArchitectureStep, ...]:
        return tuple(self._steps)

    def model_set(self) -> ModelSet:
        models = [(m.meta.id, m.meta.version) for m in self._models]
        return ModelSet.of(models + [(s.meta.id, s.meta.version) for s in self._steps])


def missing_inputs(meta: ModelMeta, facts: ComponentReliability) -> tuple[str, ...]:
    """The required properties this node lacks: an unknown value is as missing as an absent one."""
    return facts.missing(meta.configuration)


def _checked(meta: ModelMeta, node: Node, output: object) -> ModelOutput:
    """A model's output, refused if it is not what the contract says (a model bug)."""
    if not isinstance(output, ModelOutput) or not (output.estimates or output.findings):
        raise InvalidReliabilityResult(details={"fields": ["output"]})
    for estimate in output.estimates:
        if not isinstance(estimate, Estimate) or estimate.element_id != node.id:
            raise InvalidReliabilityResult(details={"fields": ["element_id"]})
        if estimate.resource not in meta.resources:
            raise InvalidReliabilityResult(details={"fields": ["resource"]})
        if estimate.model_id is not None and (estimate.model_id, estimate.model_version) != (
            meta.id,
            meta.version,
        ):
            raise InvalidReliabilityResult(details={"fields": ["model_id"]})
        if estimate.source is Source.UNKNOWN and not estimate.missing:
            raise InvalidReliabilityResult(details={"fields": ["missing"]})
    for finding in output.findings:
        if not isinstance(finding, ReliabilityFinding) or node.id not in finding.node_ids:
            raise InvalidReliabilityResult(details={"fields": ["findings"]})
    return output


def _component(
    node: Node, context: ReliabilityContext, models: tuple[ReliabilityModel, ...]
) -> tuple[ComponentResult, list[ReliabilityFinding], list[Unsupported]]:
    facts = context.facts[node.id]
    applicable = [m for m in models if node.kind in m.meta.kinds]
    inputs = facts.evidence(sorted(facts.facts))
    if not applicable:
        message = f"No reliability model supports a {node.kind} component."
        return (
            ComponentResult(node.id, ComponentStatus.UNSUPPORTED, inputs=inputs),
            [],
            [Unsupported(node.id, "no_reliability_model", message)],
        )
    applied: list[tuple[str, int]] = []
    estimates: list[Estimate] = []
    findings: list[ReliabilityFinding] = []
    lacking: dict[str, set[str]] = {}  # resource -> what would have estimated it
    failures: list[Unsupported] = []
    for model in applicable:
        meta = model.meta
        known = {e.resource for e in estimates if e.known}
        if set(meta.resources) <= known:
            continue  # an earlier model (precedence) already established all of it
        required = missing_inputs(meta, facts)
        if required:
            applied.append((meta.id, meta.version))
            for resource in meta.resources:
                lacking.setdefault(resource, set()).update(required)
            continue
        try:
            output = _checked(meta, node, model.estimate(ModelInputs(node, facts, context, tuple(estimates))))
        except InvalidReliabilityResult:
            log.error(
                "reliability model produced a malformed result",
                extra={"model_id": meta.id, "node_id": node.id},
            )
            failures.append(
                Unsupported(node.id, "invalid_output", f"The model {meta.id} produced a malformed result.")
            )
            continue
        except Exception:  # a crashing model must neither take the others down nor go unnoticed
            log.exception("reliability model failed", extra={"model_id": meta.id, "node_id": node.id})
            failures.append(Unsupported(node.id, "model_failed", f"The model {meta.id} could not run."))
            continue
        applied.append((meta.id, meta.version))
        for estimate in output.estimates:
            if estimate.known and estimate.resource in known:
                continue  # precedence: the earlier model's value stands
            estimates.append(estimate)
            if not estimate.known:
                lacking.setdefault(estimate.resource, set()).update(estimate.missing)
        findings += output.findings
    known = {e.resource for e in estimates if e.known}
    # an unknown estimate is kept only when nothing else established that resource
    estimates = [e for e in estimates if e.known or e.resource not in known]
    unique: dict[str, Estimate] = {}
    for estimate in estimates:
        unique.setdefault(estimate.resource, estimate)
    missing = sorted({m for resource, names in lacking.items() if resource not in known for m in names})
    if not applied:
        return ComponentResult(node.id, ComponentStatus.UNSUPPORTED, inputs=inputs), findings, failures
    status = ComponentStatus.ESTIMATED if known else ComponentStatus.INSUFFICIENT_INPUT
    result = ComponentResult(node.id, status, tuple(applied), tuple(unique.values()), inputs, tuple(missing))
    return result, findings, failures


def _step_output(meta: StepMeta, output: object) -> StepOutput:
    if not isinstance(output, StepOutput):
        raise InvalidReliabilityResult(details={"fields": ["output"]})
    produced = {
        "paths": bool(output.paths),
        "findings": bool(output.findings),
        "objectives": bool(output.objectives),
    }
    if any(present and kind not in meta.produces for kind, present in produced.items()):
        raise InvalidReliabilityResult(details={"fields": ["produces"]})
    checks = (
        all(isinstance(p, PathResult) for p in output.paths),
        all(isinstance(f, ReliabilityFinding) for f in output.findings),
        all(isinstance(o, ObjectiveResult) for o in output.objectives),
        all(isinstance(u, Unsupported) for u in output.unsupported),
    )
    if not all(checks):
        raise InvalidReliabilityResult(details={"fields": ["output"]})
    return output


def check_request(context: ReliabilityContext) -> None:
    """The request's entries and objective scopes name nodes of this revision: entries anything but a
    boundary, objectives components in scope. InvalidReliabilityRequest otherwise (nothing runs)."""
    topology = context.topology
    for entry in context.request.entries or ():
        node = topology.node(entry)
        if node is None or node.kind is NodeKind.BOUNDARY:
            raise InvalidReliabilityRequest(
                details={"field": "entries", "reason": "unknown_node", "node_id": entry}
            )
    for objective in context.request.objectives:
        for node_id in objective.node_ids:
            node = topology.node(node_id)
            if node is None or node.kind in OUT_OF_SCOPE:
                raise InvalidReliabilityRequest(
                    details={"field": "objectives.node_ids", "reason": "unknown_node", "node_id": node_id}
                )


def analyze(context: ReliabilityContext, registry: Registry) -> ReliabilityResult:
    """Every applicable model against every node in scope, then every step; deterministic for
    equal inputs. InvalidReliabilityRequest when the request names nodes the revision lacks."""
    check_request(context)
    models = registry.models()
    components: list[ComponentResult] = []
    findings: list[ReliabilityFinding] = []
    unsupported: list[Unsupported] = []
    for node in context.ir.nodes:  # id order
        if node.kind in OUT_OF_SCOPE:
            continue
        component, found, problems = _component(node, context, models)
        components.append(component)
        findings += found
        unsupported += problems
    paths: list[PathResult] = []
    objectives: list[ObjectiveResult] = []
    for step in registry.steps():
        progress = Progress(tuple(components), tuple(paths), tuple(findings))
        try:
            output = _step_output(step.meta, step.run(context, progress))
        except InvalidReliabilityResult:
            log.error("reliability step produced a malformed result", extra={"step_id": step.meta.id})
            unsupported.append(
                Unsupported(
                    ARCHITECTURE, "invalid_output", f"The step {step.meta.id} produced a malformed result."
                )
            )
            continue
        except Exception:  # one failing step must not take the others down, nor go unnoticed
            log.exception("reliability step failed", extra={"step_id": step.meta.id})
            unsupported.append(
                Unsupported(ARCHITECTURE, "step_failed", f"The step {step.meta.id} could not run.")
            )
            continue
        paths += output.paths
        findings += output.findings
        objectives += output.objectives
        unsupported += output.unsupported
    limitations = [NOT_MEASURED, NO_CATALOG] + ([] if components else [NO_COMPONENTS])
    return ReliabilityResult(
        model_set=registry.model_set(),
        context_fingerprint=context.fingerprint,
        components=tuple(components),
        paths=tuple(paths),
        findings=tuple(findings),
        objectives=tuple(objectives),
        unsupported=tuple(unsupported),
        limitations=tuple(limitations),
    )
