"""The capacity engine: the model contract, the registry and the orchestrator.

A **capacity model** declares what it is (``ModelMeta``: stable id and version, the node kinds it
supports, the configuration properties, workload fields and assumptions it requires, what it
estimates, its parameters and its known limitations) and turns one node's inputs into estimates
(capacity limits and resource amounts), each with its source, basis and inputs. Models never
persist anything, never call the API and never run user code: the registry is built in code.

The **orchestrator** is generic: it propagates the workload's demand through the architecture (a
separate, pluggable step), then for every node in scope (not clients, not boundaries), in id order,
runs every applicable model whose required inputs are present, and aggregates. It contains no
component-specific logic. What cannot be calculated is explicit:

- a node no model applies to is ``unsupported``;
- a model whose inputs are missing is not run; the node is ``insufficient_input`` (with the missing
  inputs named) unless another model estimated something;
- a model that raises or returns malformed output is recorded as an ``Unsupported`` entry
  (``model_failed``, ``invalid_output``) with a safe message, logged, and its node's other models
  still count: its absence proves nothing.
"""

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.capacity.errors import InvalidCapacityConfig, InvalidCapacityResult
from core.domain.capacity.results import (
    CapacityResult,
    ComponentResult,
    ComponentStatus,
    Demand,
    Estimate,
    Limitation,
    ModelSet,
    Source,
    Unsupported,
)
from core.domain.parameters import ParamSpec, parameter_problem, with_defaults

from .context import CapacityContext

log = logging.getLogger("architectos.capacity")

OUT_OF_SCOPE = frozenset({NodeKind.CLIENT, NodeKind.BOUNDARY})  # demand sources and groupings
CATALOG_UNAVAILABLE = Limitation(
    "catalog_unavailable",
    "No component catalog is available: capacities come from what the architecture declares and "
    "from documented models, never from benchmarks of a technology.",
)
NO_MEASUREMENTS = Limitation(
    "no_measurements",
    "Nothing here is measured on a running system: every number is declared, assumed or a model "
    "estimate, and says so.",
)
NO_COMPONENTS = Limitation(
    "no_components", "The architecture has no component a capacity model could apply to."
)


@dataclass(frozen=True, slots=True)
class ModelMeta:
    id: str
    version: int
    name: str
    description: str
    kinds: frozenset[NodeKind]
    resources: tuple[str, ...]  # what it estimates, e.g. ("request_rate",)
    configuration: tuple[str, ...] = ()  # required IR configuration properties
    workload: tuple[str, ...] = ()  # required workload profile fields
    assumptions: tuple[str, ...] = ()  # required assumption keys
    parameters: Mapping[str, ParamSpec] = field(default_factory=dict)
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
            "workload": list(self.workload),
            "assumptions": list(self.assumptions),
            "parameters": {
                name: {"type": p.type.value, "description": p.description, "default": p.default}
                for name, p in sorted(self.parameters.items())
            },
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class ModelInputs:
    node: Node
    context: CapacityContext
    demand: tuple[Demand, ...]  # the demand propagated to this node
    parameters: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ModelOutput:
    """At least one estimate. An unknown estimate names what it is missing."""

    limits: tuple[Estimate, ...] = ()
    resources: tuple[Estimate, ...] = ()
    notes: tuple[str, ...] = ()


class CapacityModel(Protocol):
    @property
    def meta(self) -> ModelMeta: ...

    def estimate(self, inputs: ModelInputs) -> ModelOutput: ...


class DuplicateModel(ValueError):
    """A programming error: two models registered under one id."""


@dataclass(frozen=True, slots=True)
class Propagation:
    """The demand reaching nodes and connections, and what could not be propagated."""

    nodes: Mapping[str, tuple[Demand, ...]] = field(default_factory=dict)
    connections: tuple[Demand, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()


type Propagator = Callable[[CapacityContext], Propagation]


def no_propagation(context: CapacityContext) -> Propagation:
    """No demand reaches any node: models estimate capacity only."""
    return Propagation()


class Registry:
    """The models this engine knows. Built once, in code."""

    def __init__(self, models: Iterable[CapacityModel] = ()) -> None:
        self._models: dict[str, CapacityModel] = {}
        for model in models:
            self.register(model)

    def register(self, model: CapacityModel) -> None:
        if model.meta.id in self._models:
            raise DuplicateModel(f"model {model.meta.id!r} is already registered")
        self._models[model.meta.id] = model

    def get(self, model_id: str) -> CapacityModel | None:
        return self._models.get(model_id)

    def models(self, *, kind: NodeKind | None = None) -> tuple[CapacityModel, ...]:
        """Every model (or those supporting ``kind``), in id order."""
        return tuple(m for _, m in sorted(self._models.items()) if kind is None or kind in m.meta.kinds)

    def select(
        self, models: tuple[str, ...] | None, parameters: Mapping[str, Mapping[str, Any]]
    ) -> tuple[CapacityModel, ...]:
        """The models asked for (all when None), checked: known ids, parameters only for selected
        models, only declared parameters with valid values. In id order."""
        if models is None:
            chosen = dict(self._models)
        else:
            unknown = sorted(set(models) - set(self._models))
            if unknown:
                raise InvalidCapacityConfig(details={"reason": "unknown_model", "model_id": unknown[0]})
            chosen = {m: self._models[m] for m in models}
        for model_id, given in sorted(parameters.items()):
            model = chosen.get(model_id)
            if model is None:
                raise InvalidCapacityConfig(details={"reason": "model_not_selected", "model_id": model_id})
            problem = parameter_problem(model.meta.parameters, given)
            if problem is not None:
                raise InvalidCapacityConfig(
                    details={"reason": problem[1], "model_id": model_id, "parameter": problem[0]}
                )
        return tuple(m for _, m in sorted(chosen.items()))

    def model_set(self, models: Iterable[CapacityModel]) -> ModelSet:
        return ModelSet.of((m.meta.id, m.meta.version) for m in models)


def missing_inputs(meta: ModelMeta, node: Node, context: CapacityContext) -> tuple[str, ...]:
    """The inputs ``meta`` requires that this node, the workload or the assumptions lack. An
    unknown configuration value is as missing as an absent one."""
    config = node.configuration
    missing = [f"configuration.{key}" for key in meta.configuration if key not in config.values]
    missing += [f"workload.{name}" for name in meta.workload if getattr(context.workload, name, None) is None]
    missing += [f"assumptions.{key}" for key in meta.assumptions if key not in context.assumptions]
    return tuple(missing)


def _checked(meta: ModelMeta, node: Node, output: object) -> ModelOutput:
    """A model's output, refused if it is not what the contract says (a model bug)."""
    if not isinstance(output, ModelOutput):
        raise InvalidCapacityResult(details={"fields": ["output"]})
    estimates = (*output.limits, *output.resources)
    if not estimates:
        raise InvalidCapacityResult(details={"fields": ["estimates"]})
    for estimate in estimates:
        if not isinstance(estimate, Estimate) or estimate.element_id != node.id:
            raise InvalidCapacityResult(details={"fields": ["element_id"]})
        if estimate.model_id is not None and (estimate.model_id, estimate.model_version) != (
            meta.id,
            meta.version,
        ):
            raise InvalidCapacityResult(details={"fields": ["model_id"]})
        if estimate.source is Source.UNKNOWN and not estimate.missing:
            raise InvalidCapacityResult(details={"fields": ["missing"]})
    if not all(isinstance(n, str) for n in output.notes):
        raise InvalidCapacityResult(details={"fields": ["notes"]})
    return output


def _component(
    node: Node,
    context: CapacityContext,
    models: tuple[CapacityModel, ...],
    demand: tuple[Demand, ...],
) -> tuple[ComponentResult, list[Unsupported]]:
    applicable = [m for m in models if node.kind in m.meta.kinds]
    if not applicable:
        message = f"No capacity model supports a {node.kind} component."
        return ComponentResult(node.id, ComponentStatus.UNSUPPORTED, demand=demand), [
            Unsupported(node.id, "no_model", message)
        ]
    applied: list[tuple[str, int]] = []
    limits: list[Estimate] = []
    resources: list[Estimate] = []
    notes: list[str] = []
    missing: list[str] = []
    failures: list[Unsupported] = []
    parameters = context.request.parameters
    for model in applicable:
        meta = model.meta
        lacking = missing_inputs(meta, node, context)
        if lacking:
            applied.append((meta.id, meta.version))
            missing += lacking
            continue
        inputs = ModelInputs(
            node, context, demand, with_defaults(meta.parameters, parameters.get(meta.id, {}))
        )
        try:
            output = _checked(meta, node, model.estimate(inputs))
        except InvalidCapacityResult:
            log.error(
                "capacity model produced a malformed result", extra={"model_id": meta.id, "node_id": node.id}
            )
            failures.append(
                Unsupported(node.id, "invalid_output", f"The model {meta.id} produced a malformed result.")
            )
            continue
        except Exception:  # a crashing model must neither take the others down nor go unnoticed
            log.exception("capacity model failed", extra={"model_id": meta.id, "node_id": node.id})
            failures.append(Unsupported(node.id, "model_failed", f"The model {meta.id} could not run."))
            continue
        applied.append((meta.id, meta.version))
        limits += output.limits
        resources += output.resources
        notes += output.notes
        missing += [m for e in (*output.limits, *output.resources) for m in e.missing]
    if not applied:
        return ComponentResult(node.id, ComponentStatus.UNSUPPORTED, demand=demand), failures
    known = any(e.known for e in (*limits, *resources))
    status = ComponentStatus.ESTIMATED if known else ComponentStatus.INSUFFICIENT_INPUT
    result = ComponentResult(
        node.id,
        status,
        tuple(applied),
        demand=demand,
        limits=tuple(limits),
        resources=tuple(resources),
        missing=tuple(missing),
        notes=tuple(sorted(set(notes))),
    )
    return result, failures


def analyze(
    context: CapacityContext, registry: Registry, propagate: Propagator = no_propagation
) -> CapacityResult:
    """Every selected model against every node in scope; deterministic for equal inputs.
    InvalidCapacityConfig when the request asks for what the registry does not offer."""
    request = context.request
    models = registry.select(request.models, request.parameters)
    propagation = propagate(context)
    components: list[ComponentResult] = []
    unsupported: list[Unsupported] = list(propagation.unsupported)
    for node in context.ir.nodes:  # id order
        if node.kind in OUT_OF_SCOPE:
            continue
        component, problems = _component(node, context, models, propagation.nodes.get(node.id, ()))
        components.append(component)
        unsupported += problems
    limitations = [CATALOG_UNAVAILABLE, NO_MEASUREMENTS] + ([] if components else [NO_COMPONENTS])
    return CapacityResult(
        model_set=registry.model_set(models),
        context_fingerprint=context.fingerprint,
        components=tuple(components),
        connections=propagation.connections,
        unsupported=tuple(unsupported),
        limitations=tuple(limitations),
    )
