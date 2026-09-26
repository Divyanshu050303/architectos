"""Severity overrides: a request may re-grade the findings of a non-mandatory rule (the registry
refuses overrides of mandatory rules). An override changes how much a finding matters, never
whether it exists; the finding keeps its identity."""

from dataclasses import replace

from core.domain.validation.results import Finding, Severity


def apply_override(finding: Finding, override: Severity | None) -> Finding:
    return finding if override is None else replace(finding, severity=override)
