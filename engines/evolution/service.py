"""The deterministic evolution engine behind the domain's ``EvolutionEngine`` port.

One analysis, in order: the goals' **triggers** from current evidence, the **rules**' candidates,
each candidate **validated** by the Validation Engine on its overlay, its **impact** evaluated by the
engines that model each dimension, and its **trade-offs** laid out — all against one exact baseline
revision that is never modified. Outputs are bounded: candidates and findings beyond the caps are cut
in canonical order, and the cut is stated.
"""

from collections.abc import Mapping
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.domain.engine_results import Limitation
from core.domain.evolution.candidates import BaselineRef
from core.domain.evolution.entities import EvolutionRequest
from core.domain.evolution.evidence import StoredAnalysis
from core.domain.evolution.ports import ImpactInputs
from core.domain.evolution.results import MAX_CANDIDATES, MAX_FINDINGS, EvolutionResult
from core.domain.evolution.tradeoffs import with_tradeoffs
from core.domain.evolution.validation import validate_candidate
from core.domain.evolution.values import CandidateCategory, EvidenceSource, GoalType
from core.domain.observability.ports import ObservabilityEngine
from core.domain.security.ports import SecurityEngine
from core.domain.simulations.ports import SimulationEngine
from core.domain.validation.options import RevisionInfo, ValidationConfig
from core.domain.validation.ports import ValidationEngine
from engines.observability.service import DeterministicObservabilityEngine
from engines.security.service import DeterministicSecurityEngine
from engines.simulation.service import DeterministicSimulationEngine
from engines.validation.service import DeterministicValidationEngine

from .impact import Assessor, ImpactEngines
from .rulebook import default_registry
from .rules import Registry, RuleContext, generate
from .trigger_engine import evaluate

LIMITATIONS = (
    Limitation(
        "proposals_only",
        "Candidates are proposals for engineering review. Nothing is applied: changing the architecture "
        "is a separate, authorized change, and choosing among candidates is a person's decision.",
    ),
    Limitation(
        "configuration_only",
        "Only configuration changes of existing elements are proposed. Structural changes (new "
        "components, caches, queues, splitting a service, sharding, more regions) are reported as "
        "considerations for human review: no model here evaluates them.",
    ),
    Limitation(
        "model_based",
        "Impacts are the engines' model-based projections of the architecture as declared, not "
        "measurements, and not a guarantee of production behavior.",
    ),
)


class DeterministicEvolutionEngine:
    def __init__(
        self,
        registry: Registry | None = None,
        *,
        simulation: SimulationEngine | None = None,
        security: SecurityEngine | None = None,
        observability: ObservabilityEngine | None = None,
        validation: ValidationEngine | None = None,
    ) -> None:
        self._registry = registry or default_registry()
        self._engines = ImpactEngines(
            simulation or DeterministicSimulationEngine(),
            security or DeterministicSecurityEngine(),
            observability or DeterministicObservabilityEngine(),
        )
        self._validation = validation or DeterministicValidationEngine()

    def analyze(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: EvolutionRequest,
        analyses: Mapping[EvidenceSource, StoredAnalysis],
        inputs: ImpactInputs,
    ) -> EvolutionResult:
        baseline = BaselineRef(request.architecture_id, revision.number, revision.content_hash)
        goals = {g.key: g for g in request.goals}
        evaluation = evaluate(ir, baseline, request.goals, analyses, scope=request.scope)
        context = RuleContext(ir, baseline, goals, request.constraints)
        generation = generate(context, evaluation.triggers, self._registry)
        config = ValidationConfig()
        validated_baseline = self._validation.validate(
            ir, revision, requirements=inputs.requirements, policy=inputs.policy, config=config
        )
        assessor = Assessor(ir, revision, request.architecture_id, inputs, self._engines, self._registry)
        ordered = sorted(generation.candidates, key=lambda c: (c.category.value, c.id))
        candidates = []
        for candidate in ordered[:MAX_CANDIDATES]:
            validated = validate_candidate(
                ir,
                revision,
                candidate,
                self._validation,
                requirements=inputs.requirements,
                policy=inputs.policy,
                config=config,
                baseline=validated_baseline,
            )
            candidates.append(with_tradeoffs(assessor.assess(validated), request.goals))
        findings = sorted(set((*evaluation.findings, *generation.findings)), key=lambda f: f.sort_key)
        limitations = list(LIMITATIONS)
        if len(ordered) > MAX_CANDIDATES:
            limitations.append(
                Limitation(
                    "candidates_truncated",
                    f"{len(ordered)} candidates were generated; the first {MAX_CANDIDATES} in canonical "
                    "order are evaluated. Narrow the scope or the goals to see the others.",
                )
            )
        if len(findings) > MAX_FINDINGS:
            limitations.append(
                Limitation(
                    "findings_truncated",
                    f"More than {MAX_FINDINGS} findings: the first {MAX_FINDINGS} in canonical order are "
                    "kept.",
                )
            )
        return EvolutionResult(
            baseline=baseline,
            model_set=self._registry.model_set(),
            goals=request.goals,
            candidates=tuple(candidates),
            findings=tuple(findings[:MAX_FINDINGS]),
            evidence=evaluation.evidence,
            assumptions=request.assumptions,
            limitations=tuple(limitations),
        )

    def catalog(self) -> Mapping[str, Any]:
        """The goal types, the candidate categories, the rules with their contracts, and the limits."""
        return {
            "goal_types": [t.value for t in GoalType],
            "categories": [c.value for c in CandidateCategory],
            "rules": [r.meta.to_dict() for r in self._registry.rules()],
            "limits": {"max_candidates": MAX_CANDIDATES, "max_findings": MAX_FINDINGS},
        }
