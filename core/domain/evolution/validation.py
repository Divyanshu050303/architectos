"""Candidate validation: each candidate's overlay checked by the Validation Engine's own rules, next to
the baseline checked by the same rules, requirements and policy — no rule of its own.

Findings have content-derived ids, so what the candidate **introduces** (in the overlay, not in the
baseline) and **resolves** (in the baseline, not in the overlay) is exact, with the elements each
concerns. The candidate's state:

- ``invalid``: the overlay cannot be built (an unknown element, a broken IR rule, another baseline),
  or it introduces a blocking finding (a policy the project enforces), or it turns a requirement's
  verdict to violated;
- ``unsupported``: a change the architecture domain does not define for that element (a property
  that does not apply to its kind);
- ``not_evaluable``: a rule could not run on the overlay, or a requirement it satisfied is no longer
  verifiable;
- ``valid``: none of these — **under the modeled constraints only**: not production readiness, not a
  security certification, not a guarantee the change works.

Non-blocking findings the candidate introduces are listed but do not make it invalid; the notes say
what they are.
"""

from dataclasses import replace

from core.architecture_ir.model import ArchitectureIR
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import RevisionInfo, ValidationConfig
from core.domain.validation.ports import ValidationEngine
from core.domain.validation.results import ValidationResult, Verdict

from .candidates import Candidate, ValidationNote
from .errors import InvalidCandidate
from .overlays import CandidateOverlay, apply_candidate
from .values import ValidationState

UNSUPPORTED_REASONS = frozenset({"not_applicable"})


def _refused(candidate: Candidate, error: InvalidCandidate) -> Candidate:
    reason = str(error.details.get("reason"))
    element = str(error.details.get("element_id") or "")
    prop = str(error.details.get("property") or "")
    where = f" ({element}{'.' + prop if prop else ''})" if element else ""
    note = ValidationNote(
        "overlay_refused",
        f"The candidate cannot be applied to its baseline: {reason}{where}.",
        reason,
        (element,) if element else (),
        blocking=True,
    )
    state = ValidationState.UNSUPPORTED if reason in UNSUPPORTED_REASONS else ValidationState.INVALID
    return replace(candidate, validation=state, validation_notes=(note,))


def validate_candidate(  # noqa: PLR0913 -- the candidate, its baseline, and what validation reads
    ir: ArchitectureIR,
    revision: RevisionInfo,
    candidate: Candidate,
    engine: ValidationEngine,
    *,
    requirements: tuple[Requirement, ...] = (),
    policy: ArchitecturePolicy | None = None,
    config: ValidationConfig | None = None,
    baseline: ValidationResult | None = None,
    overlay: CandidateOverlay | None = None,
) -> Candidate:
    """``candidate`` with its validation state and notes. ``baseline`` is the same engine's result
    for ``ir`` with the same inputs (computed here when not given)."""
    if overlay is None:  # built here unless the caller already has it
        try:
            overlay = apply_candidate(ir, candidate)
        except InvalidCandidate as error:
            return _refused(candidate, error)
    config = config or ValidationConfig()
    if baseline is None:
        baseline = engine.validate(ir, revision, requirements=requirements, policy=policy, config=config)
    proposed = engine.validate(
        overlay.architecture,
        replace(revision, content_hash=overlay.content_hash),
        requirements=requirements,
        policy=policy,
        config=config,
    )
    before = {f.id: f for f in baseline.findings}
    after = {f.id: f for f in proposed.findings}
    notes: list[ValidationNote] = []
    for finding_id in sorted(after.keys() - before.keys()):
        f = after[finding_id]
        notes.append(
            ValidationNote(
                "introduced_finding",
                f"The candidate introduces {f.rule_id}: {f.title}",
                finding_id,
                f.entity_ids,
                f.blocking,
            )
        )
    for finding_id in sorted(before.keys() - after.keys()):
        f = before[finding_id]
        notes.append(
            ValidationNote(
                "resolved_finding", f"The candidate resolves {f.rule_id}: {f.title}", finding_id, f.entity_ids
            )
        )
    verdicts = {r.requirement_id: r.verdict for r in baseline.requirement_results}
    for result in proposed.requirement_results:
        was = verdicts.get(result.requirement_id)
        if result.verdict is Verdict.VIOLATED and was is not Verdict.VIOLATED:
            notes.append(
                ValidationNote(
                    "requirement_violated",
                    f"The candidate would violate {result.reference}: {result.reason}",
                    result.requirement_id,
                    result.entity_ids,
                    blocking=True,
                )
            )
        elif result.verdict is Verdict.NOT_VERIFIABLE and was is Verdict.SATISFIED:
            notes.append(
                ValidationNote(
                    "requirement_not_verifiable",
                    f"{result.reference} is satisfied by the baseline but cannot be verified with the "
                    f"candidate: {result.reason}",
                    result.requirement_id,
                    result.entity_ids,
                )
            )
    notes += [
        ValidationNote(
            "rule_failed", f"Rule {failure.rule_id} could not run: {failure.message}", failure.rule_id
        )
        for failure in proposed.failures
    ]
    return replace(candidate, validation=_state(notes), validation_notes=tuple(notes))


def _state(notes: list[ValidationNote]) -> ValidationState:
    if any(n.blocking for n in notes):
        return ValidationState.INVALID
    if any(n.code in {"rule_failed", "requirement_not_verifiable"} for n in notes):
        return ValidationState.NOT_EVALUABLE
    return ValidationState.VALID
